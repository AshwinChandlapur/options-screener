r"""
CSP screener backtest — synthetic-premium walk-forward.

Built in response to the Phase-2 audit finding: the live CSP scoring function
(`backend/services/scoring/env.py` + `strike.py`) has zero empirical
validation. Every weight in ``ENV_WEIGHTS`` and ``STRIKE_WEIGHTS`` is a prior;
this script generates the first posterior.

================================================================================
What this script does (and what it does NOT)
================================================================================

For each (date, ticker) cell in a weekly walk-forward grid over the curated
``MOMENTUM_UNIVERSE``:

  1. Compute the production scoring inputs from price history *up to that date*
     only (no look-ahead): SMA ratio, SMA50 10d slope, RSI(14), 52W high
     distance, HV(30), IV-percentile-from-HV.
  2. Synthesise a strike grid around spot at strikes {0.85, 0.875, ..., 0.975}
     × spot (covers the live screener's −0.10 to −0.40 delta band at 35-DTE /
     25 % vol).
  3. For each strike, price the put with Black-Scholes using HV(30) as the IV
     proxy (audit-recommended substitute for unavailable historical chains)
     and rf = 4.5 %. Compute Δ via the live `black_scholes_put_delta`.
  4. Apply the live screener's hard filters (delta ∈ [−0.35, −0.10], strike
     within 2 % ITM tolerance — the same filter the live screener uses).
  5. Score the strike using the live `compute_env_score` + a
     **quant-isolated** strike score (Δ + ROC only, renormalised to 100 pts).
     See "Why a renormalised strike score" below.
  6. Pick the strike per (date, ticker) that maximises final score —
     production logic — and record it.
  7. Mark to expiration using the daily close at ``date + DTE`` calendar days.
     Realised P&L per contract = 100 × (premium − max(0, strike − S_T)).
     Realised annualised ROC = pnl / (100 × (strike − premium)) × (365/DTE).
  8. Bucket all trades by score quintile (and a fixed 65-cutoff bucket).
     Report per-bucket: count, mean realised ROC, Sharpe (realised ROC / std),
     assignment rate, hit rate (positive P&L), max drawdown of the cumulative
     P&L curve.
  9. Run a Spearman test: ``corr(score, realised_ROC)`` across all trades.
     The system passes the audit's monotonicity bar iff
     (a) bucket means are monotone non-decreasing in score AND
     (b) Spearman ρ > 0 with p < 0.05.

What this script does NOT model:
  - Real bid/ask spreads (no historical chain → BA factor is omitted from the
    strike score; this is the right thing to do — we are testing whether the
    *signal* has alpha, not whether the *fills* are achievable).
  - Real OI / Volume (omitted from both ENV-OI and Strike-LQ; see above).
  - Early assignment, dividends, commissions.
  - Path-dependent intra-trade adjustments (rolls, BTC at 50 % profit, etc.).
  - Universe drift (we use today's universe at all backtest dates → survivorship
    bias is acknowledged and called out in the audit as CRITICAL-2).
  - Real IV term structure / skew (HV(30) is a flat-vol approximation).

================================================================================
Why a renormalised strike score
================================================================================

The live strike score is Δ(25) + BA(25) + LQ(15) + ROC(35) = 100. Without
historical chains we cannot score BA or LQ honestly. Two wrong options would
be (a) award full BA+LQ credit to all strikes (inflates every score, kills
discrimination) or (b) award zero (deflates every score, also kills
discrimination). Instead we score only the two quant-derivable factors
(Δ + ROC, weight 25 + 35 = 60) and renormalise to a 0–100 scale via
``strike_score = (Δ_pts + ROC_pts) * 100 / 60``. The final score is then
``0.4 × ENV + 0.6 × strike_score`` exactly as production does. This isolates
the *quant alpha question* ("does the scoring math correlate with realised
P&L?") from the *microstructure question* ("are the picks fillable at mid?")
which a separate test would have to answer with paid chain data.

================================================================================
Usage
================================================================================

    cd backend
    .\venv\Scripts\python.exe ..\scripts\backtest_csp.py --years 3 --dte 35
    .\venv\Scripts\python.exe ..\scripts\backtest_csp.py --tickers NVDA,PLTR --years 2
    .\venv\Scripts\python.exe ..\scripts\backtest_csp.py --weekly-step 2 --out csp_bt.csv

================================================================================
Outputs
================================================================================

Console: per-bucket summary table + monotonicity verdict.
CSV (--out): one row per trade with all scoring inputs and outcome columns.
"""
from __future__ import annotations

import argparse
import logging
import math
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

# Force UTF-8 stdout so Unicode in summaries doesn't crash on Windows cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

import numpy as np
import pandas as pd
import yfinance as yf

# Make `services.*` imports work when run as `python scripts/backtest_csp.py`
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from services.greeks_service import black_scholes_put_delta  # noqa: E402
from services.scoring.env import compute_env_score  # noqa: E402
from services.scoring.strike import _score_delta_symmetric, _score_roc  # noqa: E402
from services.universe import MOMENTUM_UNIVERSE  # noqa: E402

logger = logging.getLogger("backtest_csp")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DTE = 35
DEFAULT_YEARS = 3
DEFAULT_WEEKLY_STEP = 1            # weeks between scan dates
DEFAULT_RF = 0.045
DELTA_GATE = (-0.35, -0.10)        # production filter (csp_service.py)
ITM_TOL = 1.02                     # production strike_filter (2 % ITM tolerance)
STRIKE_GRID_FRACTIONS = [0.85, 0.875, 0.90, 0.925, 0.95, 0.975]
SCORE_BUCKETS = [
    (0.0, 50.0, "0-50"),
    (50.0, 65.0, "50-65"),
    (65.0, 75.0, "65-75 (tradeable)"),
    (75.0, 85.0, "75-85"),
    (85.0, 100.1, "85-100"),
]

# v3.3 strike-score weights — used for renormalisation (Δ + ROC only)
STRIKE_QUANT_MAX = 25.0 + 35.0  # 60 pts of the 100-pt strike score


def _parse_env_detail(detail: str) -> dict[str, float]:
    """Parse the `compute_env_score` detail string ('IVP:25 Tr:15 SMA:5 ...').

    Returns a dict keyed by factor code with float point values. Unknown / missing
    factors default to 0. Used to extract per-factor sub-scores for the factor
    correlation analysis.
    """
    out: dict[str, float] = {}
    for token in detail.split():
        if ":" not in token:
            continue
        k, v = token.split(":", 1)
        try:
            out[k] = float(v)
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    scan_date: str
    ticker: str
    spot: float
    strike: float
    dte: int
    expiry_date: str
    # Raw scoring inputs (captured for factor-correlation analysis)
    hv30: float
    iv_pct: float
    rsi: float
    sma_ratio: float
    sma50_slope_pct: float
    dist52w: float
    delta: float
    premium: float
    # Per-factor sub-scores (parsed from compute_env_score detail string + math)
    env_IVP: float
    env_Tr: float
    env_SMA: float
    env_SLP: float
    env_RSI: float
    env_OI: float
    strike_Delta: float
    strike_ROC: float
    # Composite scores
    env_score: float
    strike_quant_score: float
    final_score: float
    spot_at_exp: float
    assigned: int                  # 1 if S_T < strike
    pnl_per_contract: float        # 100 * (premium - max(0, strike - S_T))
    realised_roc_annualised: float # pnl / (100 * (strike - premium)) * (365/DTE)
    realised_return_per_dollar: float  # pnl / (100 * (strike - premium))


# ---------------------------------------------------------------------------
# Per-bar scoring inputs (vectorised across the time axis up front, then
# sliced per scan date — much faster than recomputing per (date, ticker)).
# ---------------------------------------------------------------------------

@dataclass
class TickerSeries:
    """Pre-computed indicator series for one ticker, indexed by date."""
    df: pd.DataFrame                # cols: Close, sma_ratio, sma50_slope_pct,
                                    #       rsi, dist52w, hv30, iv_pct


def _prepare_ticker(symbol: str, start: str, end: str) -> TickerSeries | None:
    """
    Fetch and pre-compute every per-date scoring input we need.

    Uses ``auto_adjust=True`` (split / dividend-adjusted) — required for any
    historical bar-comparison work. See Phase-1 fix to backtest_swing.py.
    """
    try:
        # Pull extra history before `start` so the rolling windows are warm
        # by the time we hit the first scan date.
        warmup_start = (datetime.fromisoformat(start) - timedelta(days=400)).strftime("%Y-%m-%d")
        df = yf.Ticker(symbol).history(
            start=warmup_start, end=end, auto_adjust=True, actions=False
        )
        if df.empty or len(df) < 260:
            return None
    except Exception as exc:
        logger.warning("fetch failed for %s: %s", symbol, exc)
        return None

    close = df["Close"]
    log_ret = np.log(close / close.shift(1))

    # HV(30), annualised — rolling
    hv30 = log_ret.rolling(30).std(ddof=1) * np.sqrt(252)

    # IV-percentile-from-HV: % of last 252 days where rolling-HV < today's HV.
    # Vectorised via rolling rank.
    def _iv_pct_window(window: np.ndarray) -> float:
        today = window[-1]
        if np.isnan(today):
            return np.nan
        finite = window[~np.isnan(window)]
        if len(finite) < 60:
            return np.nan
        return float((finite < today).sum()) / len(finite) * 100.0

    iv_pct = hv30.rolling(252, min_periods=60).apply(_iv_pct_window, raw=True)

    # SMA50 / SMA200
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    sma_ratio = sma50 / sma200

    # SMA50 10-day % change
    sma50_slope_pct = (sma50 / sma50.shift(10) - 1.0) * 100.0

    # RSI(14) — Wilder's smoothing, matches services.indicators.compute_rsi
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss
    rsi = 100.0 - 100.0 / (1.0 + rs)

    # 52W high distance (% below — negative). Use rolling 252-day max.
    high_52w = close.rolling(252, min_periods=20).max()
    dist52w = (close - high_52w) / high_52w * 100.0

    out = pd.DataFrame({
        "Close": close,
        "sma_ratio": sma_ratio,
        "sma50_slope_pct": sma50_slope_pct,
        "rsi": rsi,
        "dist52w": dist52w,
        "hv30": hv30,
        "iv_pct": iv_pct,
    })
    # Drop tz info so we can index by plain date strings later
    out.index = out.index.tz_localize(None) if out.index.tz is not None else out.index
    return TickerSeries(df=out)


# ---------------------------------------------------------------------------
# Per-(date, ticker) candidate generation + scoring
# ---------------------------------------------------------------------------

def _best_csp_trade(
    *,
    ticker: str,
    scan_date: pd.Timestamp,
    series: TickerSeries,
    dte: int,
    rf: float,
) -> Trade | None:
    """
    Build the strike grid, score each, return the highest-final-score Trade
    (or None if no strike passes the production hard filters).
    """
    if scan_date not in series.df.index:
        # scan_date is a calendar Monday; map to the next valid trading day
        idx = series.df.index
        future = idx[idx >= scan_date]
        if future.empty:
            return None
        scan_date = future[0]

    row = series.df.loc[scan_date]
    spot = float(row["Close"])
    hv30 = float(row["hv30"]) if not math.isnan(row["hv30"]) else 0.25  # production fallback
    iv_pct = float(row["iv_pct"]) if not math.isnan(row["iv_pct"]) else None
    rsi = float(row["rsi"]) if not math.isnan(row["rsi"]) else float("nan")
    dist52w = float(row["dist52w"]) if not math.isnan(row["dist52w"]) else float("nan")
    sma_ratio = float(row["sma_ratio"]) if not math.isnan(row["sma_ratio"]) else 1.0
    sma50_slope_pct = float(row["sma50_slope_pct"]) if not math.isnan(row["sma50_slope_pct"]) else 0.0

    if spot <= 0 or hv30 <= 0:
        return None

    # Look up expiry close (calendar dte days forward → next available bar).
    expiry_target = scan_date + pd.Timedelta(days=dte)
    future_idx = series.df.index[series.df.index >= expiry_target]
    if future_idx.empty:
        return None  # not enough forward data
    expiry_actual = future_idx[0]
    spot_at_exp = float(series.df.loc[expiry_actual, "Close"])
    realised_dte = (expiry_actual - scan_date).days

    # ENV score (universal across strike grid for this ticker/date).
    # Parse the `detail` string into per-factor sub-scores for correlation analysis.
    env_score, env_detail = compute_env_score(
        iv_rank=None, iv_hv_ratio=None,
        price_above_sma50=False, sma50_above_sma200=False,
        dist_from_52w_high_pct=dist52w,
        rsi=rsi,
        chain_median_oi=0.0,       # unknown without chain -> 0 pts on OI
        earnings_within_dte=False, # not modelled
        direction="csp",
        sma_ratio=sma_ratio,
        sma50_slope_pct=sma50_slope_pct,
        iv_percentile=iv_pct,
    )
    env_bk = _parse_env_detail(env_detail)

    T = dte / 365.0
    best: Trade | None = None
    for frac in STRIKE_GRID_FRACTIONS:
        strike = round(spot * frac, 2)

        # Production hard filter — match strike_filter in csp_service.py line 604
        if not (strike < spot * ITM_TOL):
            continue

        delta = black_scholes_put_delta(spot, strike, rf, T, hv30)
        if not (DELTA_GATE[0] <= delta <= DELTA_GATE[1]):
            continue

        # Black-Scholes put price (synthetic premium)
        premium = _bs_put_price(spot, strike, rf, T, hv30)
        if premium <= 0:
            continue

        # Strike-quant score: Δ (25) + ROC (35), renormalised to 100.
        p_delta = _score_delta_symmetric(delta, ideal=-0.225)
        capital_per_share = strike - premium
        if capital_per_share <= 0:
            continue
        roc = (premium / capital_per_share) * (365.0 / dte) * 100.0
        p_roc = _score_roc(roc)
        strike_quant_score = (p_delta + p_roc) * 100.0 / STRIKE_QUANT_MAX

        final_score = round(0.4 * env_score + 0.6 * strike_quant_score, 1)

        # Outcome
        pnl = 100.0 * (premium - max(0.0, strike - spot_at_exp))
        capital = 100.0 * (strike - premium)
        realised_roc_ann = pnl / capital * (365.0 / realised_dte) * 100.0
        realised_per_dollar = pnl / capital

        candidate = Trade(
            scan_date=scan_date.strftime("%Y-%m-%d"),
            ticker=ticker,
            spot=round(spot, 2),
            strike=strike,
            dte=realised_dte,
            expiry_date=expiry_actual.strftime("%Y-%m-%d"),
            hv30=round(hv30, 4),
            iv_pct=round(iv_pct, 1) if iv_pct is not None else float("nan"),
            rsi=round(rsi, 1) if not math.isnan(rsi) else float("nan"),
            sma_ratio=round(sma_ratio, 4),
            sma50_slope_pct=round(sma50_slope_pct, 3),
            dist52w=round(dist52w, 2) if not math.isnan(dist52w) else float("nan"),
            delta=delta,
            premium=round(premium, 3),
            env_IVP=env_bk.get("IVP", 0.0),
            env_Tr=env_bk.get("Tr", 0.0),
            env_SMA=env_bk.get("SMA", 0.0),
            env_SLP=env_bk.get("SLP", 0.0),
            env_RSI=env_bk.get("RSI", 0.0),
            env_OI=env_bk.get("OI", 0.0),
            strike_Delta=round(p_delta, 1),
            strike_ROC=round(p_roc, 1),
            env_score=env_score,
            strike_quant_score=round(strike_quant_score, 1),
            final_score=final_score,
            spot_at_exp=round(spot_at_exp, 2),
            assigned=int(spot_at_exp < strike),
            pnl_per_contract=round(pnl, 2),
            realised_roc_annualised=round(realised_roc_ann, 2),
            realised_return_per_dollar=round(realised_per_dollar, 4),
        )
        if best is None or candidate.final_score > best.final_score:
            best = candidate

    return best


def _bs_put_price(S: float, K: float, r: float, T: float, sigma: float) -> float:
    """Black-Scholes European put price. Matches the delta function's assumptions."""
    from scipy.stats import norm  # local import keeps script header light
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return float(K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1))
    except (ValueError, ZeroDivisionError):
        return 0.0


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run_backtest(
    tickers: list[str],
    years: int,
    dte: int,
    weekly_step: int,
    rf: float,
) -> pd.DataFrame:
    end = datetime.now().date()
    start = end - timedelta(days=365 * years + dte + 30)  # extra for expiry lookup

    logger.info("Fetching %d tickers from %s to %s", len(tickers), start, end)
    cache: dict[str, TickerSeries] = {}
    for i, t in enumerate(tickers, 1):
        s = _prepare_ticker(t, start.isoformat(), end.isoformat())
        if s is not None:
            cache[t] = s
        if i % 10 == 0:
            logger.info("  prepared %d / %d", i, len(tickers))
    logger.info("Prepared %d / %d tickers (skipped %d for thin history)",
                len(cache), len(tickers), len(tickers) - len(cache))

    # Weekly scan dates from `start + 300d warmup` through `end - dte - 5d`
    scan_start = pd.Timestamp(start) + pd.Timedelta(days=300)
    scan_end = pd.Timestamp(end) - pd.Timedelta(days=dte + 5)
    scan_dates = pd.date_range(scan_start, scan_end, freq=f"{weekly_step}W-MON")

    trades: list[Trade] = []
    for sd in scan_dates:
        for t, series in cache.items():
            trade = _best_csp_trade(
                ticker=t, scan_date=sd, series=series, dte=dte, rf=rf,
            )
            if trade is not None:
                trades.append(trade)

    df = pd.DataFrame([asdict(t) for t in trades])
    logger.info("Generated %d trades across %d scan dates", len(df), len(scan_dates))
    return df


# ---------------------------------------------------------------------------
# Aggregate stats
# ---------------------------------------------------------------------------

def _max_drawdown(returns: pd.Series) -> float:
    """Max drawdown of the cumulative-sum P&L curve (per-trade returns)."""
    if returns.empty:
        return 0.0
    equity = returns.cumsum()
    peak = equity.cummax()
    dd = equity - peak
    return float(dd.min())


def summarise(df: pd.DataFrame) -> None:
    if df.empty:
        print("\nNo trades generated. Try a longer --years or different --tickers.")
        return

    print(f"\n{'=' * 78}")
    print(f"CSP BACKTEST RESULTS -- {len(df)} trades across {df['ticker'].nunique()} tickers")
    print(f"  Date range: {df['scan_date'].min()} -> {df['scan_date'].max()}")
    print(f"{'=' * 78}\n")

    # Per-bucket
    rows = []
    for lo, hi, label in SCORE_BUCKETS:
        sub = df[(df["final_score"] >= lo) & (df["final_score"] < hi)]
        if sub.empty:
            rows.append({
                "bucket": label, "n": 0,
                "mean_ROC%": np.nan, "median_ROC%": np.nan,
                "Sharpe": np.nan, "win_rate%": np.nan, "assign_rate%": np.nan,
                "max_DD$": np.nan,
            })
            continue
        roc = sub["realised_roc_annualised"]
        pnl = sub["pnl_per_contract"]
        sharpe = roc.mean() / roc.std(ddof=1) if roc.std(ddof=1) > 0 else np.nan
        rows.append({
            "bucket": label,
            "n": len(sub),
            "mean_ROC%": round(roc.mean(), 1),
            "median_ROC%": round(roc.median(), 1),
            "Sharpe": round(sharpe, 2) if not np.isnan(sharpe) else np.nan,
            "win_rate%": round((pnl > 0).mean() * 100, 1),
            "assign_rate%": round(sub["assigned"].mean() * 100, 1),
            "max_DD$": round(_max_drawdown(pnl), 0),
        })

    bucket_df = pd.DataFrame(rows)
    print("Per-score-bucket performance:")
    print(bucket_df.to_string(index=False))

    # Monotonicity test
    print("\nMonotonicity test (THE audit's headline question):")
    populated = [r for r in rows if r["n"] > 0]
    means = [r["mean_ROC%"] for r in populated]
    is_monotone = all(means[i] <= means[i + 1] for i in range(len(means) - 1))
    print(f"  Bucket means monotone non-decreasing? {'YES' if is_monotone else 'NO'}")
    print(f"  Sequence: {' -> '.join(f'{m:+.1f}' for m in means)}")

    # Spearman across all trades
    from scipy.stats import spearmanr  # type: ignore
    rho, p = spearmanr(df["final_score"], df["realised_roc_annualised"])
    print(f"  Spearman(score, realised_ROC) rho = {rho:+.3f}   p = {p:.4f}")
    verdict = "PASS" if (rho > 0 and p < 0.05) else "FAIL"
    print(f"  Verdict: {verdict} -- {'score has a monotonic relationship to realised ROC' if verdict == 'PASS' else 'no detectable signal in the scoring function on this sample'}")

    # 65-cutoff check (production threshold)
    print("\n65-cutoff check (production tradeable threshold):")
    above = df[df["final_score"] >= 65]
    below = df[df["final_score"] < 65]
    if not above.empty and not below.empty:
        diff = above["realised_roc_annualised"].mean() - below["realised_roc_annualised"].mean()
        print(f"  >=65: n={len(above)}  mean ROC = {above['realised_roc_annualised'].mean():+.1f}%   "
              f"assign rate = {above['assigned'].mean() * 100:.1f}%")
        print(f"  <65:  n={len(below)}  mean ROC = {below['realised_roc_annualised'].mean():+.1f}%   "
              f"assign rate = {below['assigned'].mean() * 100:.1f}%")
        print(f"  Delta (above - below) = {diff:+.1f}%   "
              f"{'PASS -- threshold has signal' if diff > 0 else 'FAIL -- threshold does not separate winners from losers'}")
    else:
        print("  Not enough trades on both sides of 65 to test.")

    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--years", type=int, default=DEFAULT_YEARS,
                    help=f"Years of history (default {DEFAULT_YEARS})")
    ap.add_argument("--dte", type=int, default=DEFAULT_DTE,
                    help=f"Days to expiration per trade (default {DEFAULT_DTE})")
    ap.add_argument("--weekly-step", type=int, default=DEFAULT_WEEKLY_STEP,
                    help=f"Weeks between scan dates (default {DEFAULT_WEEKLY_STEP})")
    ap.add_argument("--rf", type=float, default=DEFAULT_RF,
                    help=f"Annualised risk-free rate (default {DEFAULT_RF})")
    ap.add_argument("--tickers", type=str, default=None,
                    help="Comma-separated tickers (default: full MOMENTUM_UNIVERSE)")
    ap.add_argument("--limit", type=int, default=None,
                    help="Take the first N tickers from the universe (speed)")
    ap.add_argument("--out", type=str, default=None,
                    help="Write the per-trade ledger to this CSV path")
    args = ap.parse_args()

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = list(MOMENTUM_UNIVERSE)
    if args.limit:
        tickers = tickers[: args.limit]

    df = run_backtest(
        tickers=tickers,
        years=args.years,
        dte=args.dte,
        weekly_step=args.weekly_step,
        rf=args.rf,
    )

    summarise(df)

    if args.out:
        out_path = Path(args.out)
        df.to_csv(out_path, index=False)
        logger.info("Wrote %d trades to %s", len(df), out_path)


if __name__ == "__main__":
    main()
