"""
Fetches OHLC price history and the risk-free rate from Yahoo Finance via yfinance.
"""
from __future__ import annotations

import logging

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

_FALLBACK_RISK_FREE_RATE = 0.045  # 4.5% annual


def get_ohlc(symbol: str, period: str = "1y") -> pd.DataFrame:
    """
    Returns a DataFrame with daily OHLC columns: Open, High, Low, Close, Volume.
    Index is a DatetimeIndex (UTC-aware).
    Raises ValueError if no data is returned.
    """
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, auto_adjust=True)
    if df is None or df.empty:
        raise ValueError(f"No OHLC data returned for symbol '{symbol}'")
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.dropna(subset=["Close"], inplace=True)
    return df


def get_risk_free_rate() -> float:
    """
    Fetches the 13-week Treasury bill yield (^IRX) as a decimal annual rate.
    Falls back to FALLBACK_RISK_FREE_RATE on any error.
    """
    try:
        irx = yf.Ticker("^IRX")
        hist = irx.history(period="5d")
        if hist is not None and not hist.empty:
            rate = float(hist["Close"].iloc[-1]) / 100.0
            if 0 < rate < 1:
                return rate
    except Exception as exc:
        logger.warning("Could not fetch risk-free rate: %s — using fallback %.3f", exc, _FALLBACK_RISK_FREE_RATE)
    return _FALLBACK_RISK_FREE_RATE
