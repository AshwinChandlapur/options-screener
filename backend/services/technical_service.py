"""
Computes technical indicators from OHLC DataFrames:
  - Bollinger Bands (20, 2)
  - SMA50/SMA200 ratio (trend signal)
  - RSI(14)
  - IV Rank + IV Percentile (HV-based proxy over 252 days)
  - Volume profile support levels
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_bollinger(df: pd.DataFrame, period: int = 20, std_mult: float = 2.0) -> dict:
    """
    Returns {'bb_upper': float, 'bb_middle': float, 'bb_lower': float}
    based on the last complete window in the close series.
    """
    close = df["Close"]
    if len(close) < period:
        raise ValueError(f"Not enough data for Bollinger Bands: need {period}, got {len(close)}")
    sma = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=1)
    bb_middle = float(sma.iloc[-1])
    bb_std = float(std.iloc[-1])
    return {
        "bb_upper": round(bb_middle + std_mult * bb_std, 4),
        "bb_middle": round(bb_middle, 4),
        "bb_lower": round(bb_middle - std_mult * bb_std, 4),
    }


def compute_sma_ratio(df: pd.DataFrame) -> float:
    """
    Returns SMA50 / SMA200.
    > 1.0 → price structure is bullish (50 above 200).
    < 1.0 → bearish.
    Returns NaN if not enough data.
    """
    close = df["Close"]
    if len(close) < 200:
        return float("nan")
    sma50  = float(close.rolling(50).mean().iloc[-1])
    sma200 = float(close.rolling(200).mean().iloc[-1])
    if sma200 == 0:
        return float("nan")
    return round(sma50 / sma200, 4)


def compute_rsi(df: pd.DataFrame, period: int = 14) -> float:
    """
    Wilder-smoothed RSI(14).
    Returns float in [0, 100], or NaN if insufficient data.
    """
    close = df["Close"]
    if len(close) < period + 1:
        return float("nan")
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    # Wilder smoothing = exponential with alpha = 1/period
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain.iloc[-1] / avg_loss.iloc[-1] if avg_loss.iloc[-1] != 0 else float("inf")
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(rsi, 2)


def compute_iv_rank_percentile(
    df: pd.DataFrame,
    hv_window: int = 30,
    rank_window: int = 252,
) -> tuple[float, float]:
    """
    Uses rolling 30-day historical volatility (annualised) as an IV proxy.

    Returns (iv_rank, iv_percentile):
      iv_rank       = (HV_today - HV_min_252) / (HV_max_252 - HV_min_252) * 100
                      → How high is today's IV relative to its 52-week range.
      iv_percentile = % of days in last 252 where HV < today's HV
                      → How many days had lower IV than today.

    Both in [0, 100]. Returns (nan, nan) if not enough data.
    """
    close = df["Close"]
    if len(close) < hv_window + rank_window:
        return float("nan"), float("nan")

    log_ret = np.log(close / close.shift(1)).dropna()
    hv = log_ret.rolling(hv_window).std(ddof=1) * np.sqrt(252)
    hv = hv.dropna()

    if len(hv) < rank_window:
        return float("nan"), float("nan")

    window = hv.iloc[-rank_window:]
    current = float(hv.iloc[-1])
    hv_min, hv_max = float(window.min()), float(window.max())

    iv_rank = (
        round((current - hv_min) / (hv_max - hv_min) * 100, 2)
        if hv_max != hv_min else 50.0
    )
    iv_percentile = round(float((window < current).sum()) / len(window) * 100, 2)
    return iv_rank, iv_percentile


def compute_volume_support(df: pd.DataFrame, n_bins: int = 50, lookback: int = 252) -> list[float]:
    """
    Volume Profile support levels.

    Steps:
      1. Take up to `lookback` trading days of OHLC + Volume.
      2. Compute typical price = (H + L + C) / 3 per day.
      3. Bin typical prices into `n_bins` equal-width buckets.
      4. Sum volume in each bucket.
      5. Keep only buckets whose midpoint is below today's close.
      6. Return the midpoints of the top-3 buckets by volume,
         sorted descending (nearest support first).

    Returns a list of 0–3 floats. Empty list if insufficient data.
    """
    data = df.tail(lookback).copy()
    if len(data) < 20:
        return []

    current_price = float(data["Close"].iloc[-1])
    typical = (data["High"] + data["Low"] + data["Close"]) / 3.0

    try:
        bins = pd.cut(typical, bins=n_bins)
        vol_by_bin = data["Volume"].groupby(bins).sum()

        # Midpoint of each bin interval
        bin_mids = pd.Series(
            [interval.mid for interval in vol_by_bin.index],
            index=vol_by_bin.index,
        )

        # Only levels below current price
        below_mask = bin_mids < current_price
        below_vol = vol_by_bin[below_mask]
        below_mids = bin_mids[below_mask]

        if below_vol.empty:
            return []

        top3_labels = below_vol.nlargest(3).index
        support_prices = sorted(
            [float(below_mids[lbl]) for lbl in top3_labels],
            reverse=True,  # nearest (highest) first
        )
        return [round(p, 2) for p in support_prices]

    except Exception:
        return []


def compute_rvol(df: pd.DataFrame, period: int = 20) -> float:
    """Relative volume: today's volume / avg(volume, last `period` days excluding today)."""
    vol = df["Volume"]
    if len(vol) < period + 1:
        return float("nan")
    avg_vol = float(vol.iloc[-(period + 1):-1].mean())
    if avg_vol == 0:
        return float("nan")
    return round(float(vol.iloc[-1]) / avg_vol, 2)


def compute_roc(df: pd.DataFrame, period: int = 21) -> float:
    """Rate of Change: % price change over `period` trading days."""
    close = df["Close"]
    if len(close) < period + 1:
        return float("nan")
    past = float(close.iloc[-(period + 1)])
    if past == 0:
        return float("nan")
    return round((float(close.iloc[-1]) - past) / past * 100, 2)


def compute_price_vs_52w_high(df: pd.DataFrame) -> float:
    """Returns % distance from 52-week high. 0 = at high, -10 = 10% below high."""
    close = df["Close"]
    lookback = min(252, len(close))
    if lookback < 20:
        return float("nan")
    high_52w = float(close.iloc[-lookback:].max())
    if high_52w == 0:
        return float("nan")
    return round((float(close.iloc[-1]) - high_52w) / high_52w * 100, 2)


def compute_sma20_slope(df: pd.DataFrame, n: int = 5) -> float:
    """% change in SMA20 over the last `n` days (short-term trend acceleration)."""
    close = df["Close"]
    if len(close) < 20 + n:
        return float("nan")
    sma20 = close.rolling(20).mean()
    past_sma = float(sma20.iloc[-(n + 1)])
    if past_sma == 0:
        return float("nan")
    return round((float(sma20.iloc[-1]) - past_sma) / past_sma * 100, 4)


def compute_price_vs_sma(df: pd.DataFrame, period: int = 20) -> float:
    """% by which current price is above/below SMA(period)."""
    close = df["Close"]
    if len(close) < period:
        return float("nan")
    sma = float(close.rolling(period).mean().iloc[-1])
    if sma == 0:
        return float("nan")
    return round((float(close.iloc[-1]) - sma) / sma * 100, 2)


def compute_dist_from_sma200(df: pd.DataFrame) -> float:
    """% above SMA200. Positive = above (bullish), negative = below."""
    close = df["Close"]
    if len(close) < 200:
        return float("nan")
    sma200 = float(close.rolling(200).mean().iloc[-1])
    if sma200 == 0:
        return float("nan")
    return round((float(close.iloc[-1]) - sma200) / sma200 * 100, 2)


def compute_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """
    Returns {'macd': float, 'signal': float, 'histogram': float}.
    histogram > 0 and growing = bullish momentum.
    """
    close = df["Close"]
    nan = float("nan")
    if len(close) < slow + signal:
        return {"macd": nan, "signal": nan, "histogram": nan}
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return {
        "macd":      round(float(macd_line.iloc[-1]), 4),
        "signal":    round(float(signal_line.iloc[-1]), 4),
        "histogram": round(float(histogram.iloc[-1]), 4),
    }


def compute_momentum_score(
    rvol: float,
    rsi: float,
    dist_from_52w_high_pct: float,
    sma_ratio: float,
    roc_21: float,
) -> float:
    """
    Composite momentum score 0–100. Weights:
      RVOL                  30 pts  (3× avg vol = max)
      RSI in 55–72 zone     20 pts
      Price near 52w high   25 pts  (<5% below = near max)
      SMA50/200 ratio       15 pts  (1.10+ = max)
      ROC(21)               10 pts  (10%+ = max)
    """
    import math as _math
    score = 0.0

    if not _math.isnan(rvol) and rvol > 0:
        score += min(rvol / 3.0, 1.0) * 30

    if not _math.isnan(rsi):
        if 55 <= rsi <= 72:
            score += 20
        elif (50 <= rsi < 55) or (72 < rsi <= 80):
            score += 12
        elif 45 <= rsi < 50:
            score += 5

    if not _math.isnan(dist_from_52w_high_pct):
        pct_below = abs(min(dist_from_52w_high_pct, 0.0))
        score += max(0.0, 1.0 - pct_below / 20.0) * 25

    if not _math.isnan(sma_ratio):
        score += min(max((sma_ratio - 1.0) / 0.10, 0.0), 1.0) * 15

    if not _math.isnan(roc_21):
        score += min(max(roc_21 / 10.0, 0.0), 1.0) * 10

    return round(score, 1)


def compute_csp_score(
    iv_rank: float | None,
    annualized_return: float,
    sma_ratio: float,
    rsi: float,
    delta: float,
    bid_ask_spread_pct: float | None,
    earnings_within_dte: bool,
) -> float:
    """
    Composite CSP quality score 0-100. Weights:
      IV Rank          25 pts  (>=50 = full; selling expensive premium)
      Ann. Return      20 pts  (>=25% ann = full; yield quality)
      SMA Ratio        20 pts  (>=1.05 = full; uptrend structure)
      RSI zone         15 pts  (40-65 = full; avoid overbought/oversold)
      Delta quality    10 pts  (-0.20 to -0.30 = full; ideal probability)
      Spread %         10 pts  (<=3% = full; liquidity)
      Earnings in DTE  -15 pts (hard penalty for earnings risk)
    Clipped to [0, 100].
    """
    import math as _math
    score = 0.0

    # IV Rank (25 pts)
    if iv_rank is not None and not _math.isnan(iv_rank):
        if iv_rank >= 50:
            score += 25.0
        elif iv_rank >= 30:
            score += 12.5 + (iv_rank - 30) / 20.0 * 12.5
        else:
            score += max(0.0, iv_rank / 30.0) * 12.5

    # Annualized Return (20 pts)
    if not _math.isnan(annualized_return):
        if annualized_return >= 25:
            score += 20.0
        elif annualized_return >= 15:
            score += 10.0 + (annualized_return - 15) / 10.0 * 10.0
        elif annualized_return >= 8:
            score += (annualized_return - 8) / 7.0 * 10.0

    # SMA Ratio / Trend (20 pts)
    if not _math.isnan(sma_ratio):
        if sma_ratio >= 1.05:
            score += 20.0
        elif sma_ratio >= 1.0:
            score += (sma_ratio - 1.0) / 0.05 * 20.0
        # < 1.0 -> bearish -> 0 pts

    # RSI zone (15 pts)
    if not _math.isnan(rsi):
        if 40 <= rsi <= 65:
            score += 15.0
        elif (35 <= rsi < 40) or (65 < rsi <= 70):
            score += 8.0
        elif (30 <= rsi < 35) or (70 < rsi <= 75):
            score += 3.0

    # Delta quality (10 pts)
    if not _math.isnan(delta):
        if -0.30 <= delta <= -0.20:
            score += 10.0
        elif (-0.35 <= delta < -0.30) or (-0.20 < delta <= -0.15):
            score += 5.0

    # Spread % (10 pts)
    if bid_ask_spread_pct is not None and not _math.isnan(bid_ask_spread_pct):
        if bid_ask_spread_pct <= 3.0:
            score += 10.0
        elif bid_ask_spread_pct <= 5.0:
            score += 7.0
        elif bid_ask_spread_pct <= 10.0:
            score += 3.0

    # Earnings penalty
    if earnings_within_dte:
        score -= 15.0

    return round(max(0.0, min(100.0, score)), 1)
