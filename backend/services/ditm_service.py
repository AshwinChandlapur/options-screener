"""
Orchestrates per-symbol DITM (Deep In The Money) Long Call analysis:
  OHLC → Technicals → Calls chain → Strike (delta ≥ 0.80) → Derived metrics
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date
from typing import Optional

from services.data_service import get_ohlc
from services.greeks_service import black_scholes_call_delta
from services.options_service import (
    get_bid_ask_spread_pct,
    get_calls_data,
    get_implied_volatility,
    get_open_interest,
    get_premium,
    select_ditm_call,
)
from services.technical_service import (
    compute_iv_rank_percentile,
    compute_rsi,
    compute_sma_ratio,
)

logger = logging.getLogger(__name__)


@dataclass
class DitmResult:
    symbol: str
    price: float
    sma_ratio: float            # SMA50 / SMA200
    rsi: float                  # RSI(14)
    iv_rank: Optional[float]    # HV-based IV rank 0–100
    iv_percentile: Optional[float]
    earnings_date: Optional[str]
    earnings_within_dte: bool
    strike: float
    strike_is_fallback: bool    # True = no strike met delta threshold
    expiration: str
    dte: int
    premium: float              # mid-price of the call
    delta: float                # BS call delta (0–1)
    extrinsic_value: float      # premium – intrinsic value
    extrinsic_pct: float        # extrinsic_value / stock_price × 100
    moneyness_pct: float        # (price – strike) / price × 100
    leverage_ratio: float       # price / premium  (effective shares per $ equiv)
    breakeven_price: float      # strike + premium
    breakeven_pct_above: float  # % stock must rise to break even
    capital_at_risk: float      # premium × 100  ($ per contract)
    vs_stock_cost_pct: float    # capital_at_risk / (price × 100) × 100
    bid_ask_spread_pct: Optional[float]
    open_interest: Optional[int]


@dataclass
class DitmError:
    symbol: str
    reason: str


def process_ditm_symbol(
    symbol: str,
    min_dte: int = 180,
    max_dte: int = 365,
    rf_rate: float = 0.045,
    min_delta: float = 0.80,
) -> tuple[Optional[DitmResult], Optional[DitmError]]:
    """
    Processes a single symbol for the DITM screener.
    Returns (result, None) on success or (None, error) on failure.
    """
    sym = symbol.strip().upper()
    try:
        # 1. Price history and technical indicators
        df = get_ohlc(sym, period="2y")
        current_price = float(df["Close"].iloc[-1])

        sma_ratio = compute_sma_ratio(df)
        rsi = compute_rsi(df)
        iv_rank_raw, iv_pct_raw = compute_iv_rank_percentile(df)
        iv_rank: Optional[float] = None if math.isnan(iv_rank_raw) else iv_rank_raw
        iv_percentile: Optional[float] = None if math.isnan(iv_pct_raw) else iv_pct_raw

        # 2. Call options chain
        opts = get_calls_data(sym, min_dte, max_dte)
        dte = opts["dte"]
        calls_df = opts["calls_df"]
        expiration = opts["expiration"]
        earnings_date = opts["earnings_date"]

        # Earnings-within-DTE flag
        earnings_within_dte = False
        if earnings_date:
            try:
                ed = date.fromisoformat(earnings_date)
                today = date.today()
                if 0 <= (ed - today).days <= dte:
                    earnings_within_dte = True
            except ValueError:
                pass

        # 3. Strike selection — deepest ITM call with delta >= min_delta
        T = dte / 365.0
        strike, strike_is_fallback = select_ditm_call(
            calls_df, current_price, rf_rate, T, min_delta
        )

        # 4. Premium (mid-price)
        premium = get_premium(calls_df, strike)

        # 5. Spread & liquidity
        spread_raw = get_bid_ask_spread_pct(calls_df, strike)
        bid_ask_spread_pct: Optional[float] = None if math.isnan(spread_raw) else spread_raw

        oi_raw = get_open_interest(calls_df, strike)
        open_interest: Optional[int] = None if oi_raw < 0 else oi_raw

        # 6. Delta using chain IV; fall back to 30-day HV
        sigma = get_implied_volatility(calls_df, strike)
        if math.isnan(sigma) or sigma <= 0:
            import numpy as np
            log_ret = (df["Close"] / df["Close"].shift(1)).apply(math.log).dropna()
            if len(log_ret) >= 30:
                sigma = float(log_ret.iloc[-30:].std(ddof=1) * (252 ** 0.5))
            else:
                sigma = 0.25

        delta = black_scholes_call_delta(current_price, strike, rf_rate, T, sigma)

        # 7. Derived DITM metrics
        intrinsic = max(0.0, current_price - strike)
        extrinsic_value = round(max(0.0, premium - intrinsic), 4)
        extrinsic_pct = round(extrinsic_value / current_price * 100.0, 4) if current_price > 0 else 0.0
        moneyness_pct = round((current_price - strike) / current_price * 100.0, 4) if current_price > 0 else 0.0
        leverage_ratio = round(current_price / premium, 4) if premium > 0 else 0.0
        breakeven_price = round(strike + premium, 4)
        breakeven_pct_above = round((breakeven_price - current_price) / current_price * 100.0, 4) if current_price > 0 else 0.0
        capital_at_risk = round(premium * 100.0, 2)
        vs_stock_cost_pct = round(capital_at_risk / (current_price * 100.0) * 100.0, 4) if current_price > 0 else 0.0

        result = DitmResult(
            symbol=sym,
            price=round(current_price, 4),
            sma_ratio=sma_ratio,
            rsi=rsi,
            iv_rank=iv_rank,
            iv_percentile=iv_percentile,
            earnings_date=earnings_date,
            earnings_within_dte=earnings_within_dte,
            strike=strike,
            strike_is_fallback=strike_is_fallback,
            expiration=expiration,
            dte=dte,
            premium=round(premium, 4),
            delta=delta,
            extrinsic_value=extrinsic_value,
            extrinsic_pct=extrinsic_pct,
            moneyness_pct=moneyness_pct,
            leverage_ratio=leverage_ratio,
            breakeven_price=breakeven_price,
            breakeven_pct_above=breakeven_pct_above,
            capital_at_risk=capital_at_risk,
            vs_stock_cost_pct=vs_stock_cost_pct,
            bid_ask_spread_pct=bid_ask_spread_pct,
            open_interest=open_interest,
        )
        return result, None

    except Exception as exc:
        logger.warning("DITM %s failed: %s", sym, exc, exc_info=True)
        return None, DitmError(symbol=sym, reason=str(exc))
