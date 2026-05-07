"""
EM Rank screener service.

Selects the put strike just below the 1σ Expected Move lower bound
(price − HV-derived EM) and ranks symbols by annualized ROC at that strike.
No scoring — purely mechanical strike selection + yield ranking.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import numpy as np

from services.data_service import get_ohlc
from services.greeks_service import black_scholes_put_delta
from services.indicators import (
    compute_bollinger,
    compute_iv_rank_percentile,
    compute_price_vs_52w_high,
    compute_rsi,
    compute_sma_ratio,
    compute_volume_support,
)
from services.options_service import (
    get_all_expirations_data,
    get_bid_ask_spread_pct,
    get_implied_volatility,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class EmRankStrike:
    strike: float
    bid: float
    ask: float
    mid: float
    spread_pct: Optional[float]
    delta: float
    oi_vol: int
    roc_annualized: Optional[float]
    otm_pct: float
    is_em_strike: bool          # True = this is the EM-boundary strike
    iv_fallback: bool           # True = hv_sigma used instead of yfinance IV
    stale_premium: bool         # True = lastPrice used instead of (bid+ask)/2


@dataclass
class EmRankResult:
    symbol: str
    price: float
    bb_upper: float
    bb_middle: float
    bb_lower: float
    sma_ratio: float
    rsi: float
    iv_rank: Optional[float]
    iv_percentile: Optional[float]
    earnings_date: Optional[str]
    earnings_within_dte: bool
    vol_support_126_1: Optional[float]
    vol_support_126_2: Optional[float]
    vol_support_126_3: Optional[float]
    dte: int
    expiration: str
    expected_move: float         # price × hv_sigma × √(dte/365)
    chain_median_oi: float
    dist_from_52w_high_pct: float
    iv_hv_ratio: Optional[float]
    strikes: list[EmRankStrike] = field(default_factory=list)
    best_roc: float = 0.0        # max ROC of is_em_strike rows in this expiration
    using_hv_fallback: bool = False


@dataclass
class EmRankError:
    symbol: str
    reason: str


# ---------------------------------------------------------------------------
# Per-symbol processor
# ---------------------------------------------------------------------------

def process_em_symbol(
    symbol: str,
    min_dte: int = 30,
    max_dte: int = 60,
    rf_rate: float = 0.045,
    max_capital: Optional[float] = None,
) -> tuple[list[EmRankResult], Optional[EmRankError]]:
    """
    Processes a single symbol across all valid expirations in [min_dte, max_dte].

    Strike selection logic per expiration:
      - em_target = price − (price × hv_sigma × √(dte/365))
      - em_strike = highest put strike ≤ em_target (closest from below)
      - up to 2 alternates = next 2 strikes immediately above em_strike
      - max_capital filter: strike × 100 ≤ max_capital (applied per strike)

    Returns (results, None) on success, ([], error) on any top-level failure.
    """
    sym = symbol.strip().upper()
    try:
        # ── 1. Price history & indicators ────────────────────────────────
        df = get_ohlc(sym, period="2y")
        current_price = float(df["Close"].iloc[-1])

        bb = compute_bollinger(df)
        sma_ratio = compute_sma_ratio(df)
        rsi = compute_rsi(df)
        dist_52w = compute_price_vs_52w_high(df)
        iv_rank_raw, iv_pct_raw = compute_iv_rank_percentile(df)
        iv_rank: Optional[float] = None if math.isnan(iv_rank_raw) else iv_rank_raw
        iv_percentile: Optional[float] = None if math.isnan(iv_pct_raw) else iv_pct_raw
        vol_supports_126 = compute_volume_support(df, lookback=126)

        log_ret = np.log(df["Close"] / df["Close"].shift(1)).dropna()
        hv_sigma = (
            float(log_ret.iloc[-30:].std(ddof=1) * np.sqrt(252))
            if len(log_ret) >= 30 else 0.25
        )

        # Market-open detection (US Eastern time)
        try:
            import pytz as _pytz
            from datetime import datetime as _dt
            _et = _pytz.timezone("America/New_York")
            _now = _dt.now(_et)
            _market_open = (
                _now.weekday() < 5
                and 9 * 60 + 30 <= _now.hour * 60 + _now.minute < 16 * 60
            )
        except Exception:
            _market_open = False

        # ── 2. Option chains ──────────────────────────────────────────────
        all_exps = get_all_expirations_data(sym, min_dte, max_dte)

        results: list[EmRankResult] = []
        symbol_iv_hv_ratio: Optional[float] = None  # first non-None value seen

        for opts in all_exps:
            try:
                import pandas as _pd

                dte = opts["dte"]
                puts_df = opts["puts_df"]
                earnings_date = opts["earnings_date"]
                expiration = opts["expiration"]

                # Earnings-within-DTE flag
                earnings_within_dte = False
                if earnings_date:
                    try:
                        ed = date.fromisoformat(earnings_date)
                        days_to_earnings = (ed - date.today()).days
                        if 0 <= days_to_earnings <= dte:
                            earnings_within_dte = True
                    except ValueError:
                        pass

                T = dte / 365.0
                em = round(current_price * hv_sigma * math.sqrt(T), 2)
                em_target = current_price - em  # 1σ lower bound

                # Strikes ascending, OTM only
                all_strikes_asc = sorted(puts_df["strike"].unique())
                otm_asc = [s for s in all_strikes_asc if s < current_price]

                # EM strike = highest strike ≤ em_target
                em_candidates = [s for s in otm_asc if s <= em_target]
                if not em_candidates:
                    continue  # no strike below EM boundary — skip this expiration

                em_strike_price = em_candidates[-1]  # closest from below
                em_idx = otm_asc.index(em_strike_price)

                # Alternates: next 2 strikes immediately above em_strike (inside EM)
                alt_strikes = otm_asc[em_idx + 1 : em_idx + 3]
                selected_strikes = [em_strike_price] + alt_strikes

                # Chain median OI for 0.10–0.40 delta range (independent of premium)
                _chain_ois: list[int] = []
                for sp in otm_asc:
                    try:
                        row = puts_df[puts_df["strike"] == sp]
                        if row.empty:
                            continue
                        sig_r = get_implied_volatility(puts_df, sp)
                        iv_stale_c = math.isnan(sig_r) or sig_r <= 0.01
                        sig_c = sig_r if not iv_stale_c else hv_sigma
                        d_c = black_scholes_put_delta(current_price, sp, rf_rate, T, sig_c)
                        oi_c = int(row["openInterest"].iloc[0]) if not _pd.isna(row["openInterest"].iloc[0]) else 0
                        if 0.1 < abs(d_c) < 0.4:
                            _chain_ois.append(oi_c)
                    except Exception:
                        continue
                chain_median_oi = float(np.median(_chain_ois)) if _chain_ois else 0.0

                # ── Per-strike computation ────────────────────────────────
                strike_results: list[EmRankStrike] = []
                for sp in selected_strikes:
                    try:
                        row = puts_df[puts_df["strike"] == sp]
                        if row.empty:
                            continue

                        # Capital filter
                        if max_capital is not None and sp * 100 > max_capital:
                            continue

                        bid = float(row["bid"].iloc[0]) if not _pd.isna(row["bid"].iloc[0]) else 0.0
                        ask = float(row["ask"].iloc[0]) if not _pd.isna(row["ask"].iloc[0]) else 0.0
                        last = float(row["lastPrice"].iloc[0]) if not _pd.isna(row["lastPrice"].iloc[0]) else 0.0
                        oi_val = int(row["openInterest"].iloc[0]) if not _pd.isna(row["openInterest"].iloc[0]) else 0
                        vol_val = int(row["volume"].iloc[0]) if not _pd.isna(row["volume"].iloc[0]) else 0

                        sig_raw = get_implied_volatility(puts_df, sp)
                        iv_stale = math.isnan(sig_raw) or sig_raw <= 0.01
                        used_hv = False
                        iv_hv_val: Optional[float] = None
                        if iv_stale:
                            sig = hv_sigma
                            used_hv = True
                        else:
                            sig = sig_raw
                            if hv_sigma > 0:
                                iv_hv_val = round(sig / hv_sigma, 4)
                                if symbol_iv_hv_ratio is None:
                                    symbol_iv_hv_ratio = iv_hv_val

                        delta = black_scholes_put_delta(current_price, sp, rf_rate, T, sig)

                        if bid > 0 and ask > 0:
                            mid = round((bid + ask) / 2.0, 4)
                            stale_prem = False
                        elif last > 0:
                            mid = round(last, 4)
                            bid = last
                            ask = last
                            stale_prem = True
                        else:
                            continue  # no usable premium

                        spread_raw = get_bid_ask_spread_pct(puts_df, sp)
                        spread_pct: Optional[float] = None if math.isnan(spread_raw) else spread_raw

                        capital = sp - mid
                        roc = (
                            round((mid / capital) * (365.0 / dte) * 100, 2)
                            if capital > 0 and dte > 0 else None
                        )
                        otm_pct = round((current_price - sp) / current_price * 100, 2)
                        oi_vol = vol_val if (_market_open and vol_val > 0) else oi_val

                        strike_results.append(EmRankStrike(
                            strike=sp,
                            bid=round(bid, 4),
                            ask=round(ask, 4),
                            mid=round(mid, 4),
                            spread_pct=spread_pct,
                            delta=delta,
                            oi_vol=oi_vol,
                            roc_annualized=roc,
                            otm_pct=otm_pct,
                            is_em_strike=(sp == em_strike_price),
                            iv_fallback=used_hv,
                            stale_premium=stale_prem,
                        ))
                    except Exception:
                        continue

                # Must have at least the EM strike to include this expiration
                em_rows = [s for s in strike_results if s.is_em_strike]
                if not em_rows:
                    continue

                best_roc_val = max(
                    (s.roc_annualized for s in em_rows if s.roc_annualized is not None),
                    default=0.0,
                )

                results.append(EmRankResult(
                    symbol=sym,
                    price=round(current_price, 4),
                    bb_upper=bb["bb_upper"],
                    bb_middle=bb["bb_middle"],
                    bb_lower=bb["bb_lower"],
                    sma_ratio=sma_ratio,
                    rsi=rsi,
                    iv_rank=iv_rank,
                    iv_percentile=iv_percentile,
                    earnings_date=earnings_date,
                    earnings_within_dte=earnings_within_dte,
                    vol_support_126_1=vol_supports_126[0] if len(vol_supports_126) > 0 else None,
                    vol_support_126_2=vol_supports_126[1] if len(vol_supports_126) > 1 else None,
                    vol_support_126_3=vol_supports_126[2] if len(vol_supports_126) > 2 else None,
                    dte=dte,
                    expiration=expiration,
                    expected_move=em,
                    chain_median_oi=chain_median_oi,
                    dist_from_52w_high_pct=round(dist_52w, 2),
                    iv_hv_ratio=symbol_iv_hv_ratio,
                    strikes=strike_results,
                    best_roc=best_roc_val,
                    using_hv_fallback=any(s.iv_fallback for s in strike_results),
                ))
            except Exception as exc:
                logger.debug(
                    "Skipping expiration %s for %s: %s",
                    opts.get("expiration"), sym, exc,
                )
                continue

        if not results:
            return [], EmRankError(
                symbol=sym,
                reason="No valid expirations with a strike below the EM boundary",
            )
        return results, None

    except Exception as exc:
        logger.warning("Failed to process EM rank for '%s': %s", sym, exc)
        return [], EmRankError(symbol=sym, reason=str(exc))
