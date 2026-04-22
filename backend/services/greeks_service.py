"""
Black-Scholes Greeks for European options.
yfinance does not provide exchange-supplied Greeks, so we compute delta here.
"""
from __future__ import annotations

import math

from scipy.stats import norm  # type: ignore


def black_scholes_put_delta(
    S: float,
    K: float,
    r: float,
    T: float,
    sigma: float,
) -> float:
    """
    Compute the Black-Scholes delta for a European put option.

    Parameters
    ----------
    S     : Current underlying price
    K     : Strike price
    r     : Annualised risk-free rate (decimal, e.g. 0.045)
    T     : Time to expiration in years (e.g. 35/365)
    sigma : Annualised implied volatility (decimal, e.g. 0.28)

    Returns
    -------
    float
        Put delta in (-1, 0). Returns -0.5 on degenerate inputs so callers
        always receive a numeric value.
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return -0.5  # degenerate — near-expiry or zero IV

    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        put_delta = norm.cdf(d1) - 1.0  # equivalent to -N(-d1)
        return round(put_delta, 4)
    except (ValueError, ZeroDivisionError):
        return -0.5


def black_scholes_call_delta(
    S: float,
    K: float,
    r: float,
    T: float,
    sigma: float,
) -> float:
    """
    Compute the Black-Scholes delta for a European call option.

    Returns
    -------
    float
        Call delta in (0, 1). Returns 0.5 on degenerate inputs.
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.5

    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        return round(float(norm.cdf(d1)), 4)
    except (ValueError, ZeroDivisionError):
        return 0.5
