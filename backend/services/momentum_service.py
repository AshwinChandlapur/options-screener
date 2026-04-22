"""
Per-symbol Momentum screener:
  OHLC → RVOL, ROC(21), RSI(14), SMA structure, MACD, 52w high proximity → Score
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

import yfinance as yf

from services.data_service import get_ohlc
from services.technical_service import (
    compute_dist_from_sma200,
    compute_macd,
    compute_momentum_score,
    compute_price_vs_52w_high,
    compute_price_vs_sma,
    compute_roc,
    compute_rsi,
    compute_rvol,
    compute_sma20_slope,
    compute_sma_ratio,
)

logger = logging.getLogger(__name__)


@dataclass
class MomentumResult:
    symbol: str
    price: float
    price_change_1d_pct: float       # today's % change
    rvol: float                      # relative volume vs 20-day avg
    rsi: float                       # RSI(14)
    roc_21: float                    # 21-day rate of change %
    sma_ratio: float                 # SMA50 / SMA200
    sma20_slope_pct: float           # SMA20 5-day slope %
    price_vs_sma20_pct: float        # % above/below SMA20
    dist_from_52w_high_pct: float    # % below 52w high (0 = at high, -10 = 10% below)
    dist_from_sma200_pct: float      # % above SMA200
    macd_histogram: float
    high_52w: float
    low_52w: float
    short_ratio: Optional[float]     # days to cover short (squeeze fuel)
    momentum_score: float            # composite 0–100


@dataclass
class MomentumError:
    symbol: str
    reason: str


def process_momentum_symbol(
    symbol: str,
) -> tuple[Optional[MomentumResult], Optional[MomentumError]]:
    """
    Processes a single symbol for the Momentum screener.
    Returns (result, None) on success or (None, error) on failure.
    """
    sym = symbol.strip().upper()
    try:
        df = get_ohlc(sym, period="1y")
        current_price = float(df["Close"].iloc[-1])

        # 1-day price change
        price_change_1d_pct = float("nan")
        if len(df) >= 2:
            prev = float(df["Close"].iloc[-2])
            if prev > 0:
                price_change_1d_pct = round((current_price - prev) / prev * 100, 2)

        # 52w high / low from full 1y OHLC
        high_52w = round(float(df["High"].max()), 2)
        low_52w  = round(float(df["Low"].min()), 2)

        rvol               = compute_rvol(df)
        rsi                = compute_rsi(df)
        roc_21             = compute_roc(df, 21)
        sma_ratio          = compute_sma_ratio(df)
        sma20_slope_pct    = compute_sma20_slope(df)
        price_vs_sma20_pct = compute_price_vs_sma(df, 20)
        dist_52w_high      = compute_price_vs_52w_high(df)
        dist_sma200        = compute_dist_from_sma200(df)
        macd               = compute_macd(df)
        score              = compute_momentum_score(rvol, rsi, dist_52w_high, sma_ratio, roc_21)

        # Short ratio from yfinance .info — optional, fails gracefully
        short_ratio: Optional[float] = None
        try:
            info = yf.Ticker(sym).info
            sr = info.get("shortRatio")
            if sr is not None:
                short_ratio = round(float(sr), 2)
        except Exception:
            pass

        result = MomentumResult(
            symbol=sym,
            price=round(current_price, 4),
            price_change_1d_pct=price_change_1d_pct,
            rvol=rvol,
            rsi=rsi,
            roc_21=roc_21,
            sma_ratio=sma_ratio,
            sma20_slope_pct=sma20_slope_pct,
            price_vs_sma20_pct=price_vs_sma20_pct,
            dist_from_52w_high_pct=dist_52w_high,
            dist_from_sma200_pct=dist_sma200,
            macd_histogram=macd["histogram"],
            high_52w=high_52w,
            low_52w=low_52w,
            short_ratio=short_ratio,
            momentum_score=score,
        )
        return result, None

    except Exception as exc:
        logger.warning("Momentum %s failed: %s", sym, exc, exc_info=True)
        return None, MomentumError(symbol=sym, reason=str(exc))
