r"""
CSP rank-stability snapshot tool -- validates audit Concern HIGH-4-adjacent:
"How much do rankings change between 10:00 and 15:55 ET on the same day?"

The audit's Phase 2 plan asks for Spearman >= 0.85 across intraday snapshots.
A low value means the 65-cutoff is sorting on intra-day noise rather than
signal; a high value means the same scan at different times of day picks
the same names in the same order.

Two modes
---------

CAPTURE (one-shot)
    Runs the live CSP screener (`services.csp_service.process_symbol`) over
    the configured universe and writes one snapshot file
    ``rank_snapshot_<HHMM>.csv`` containing ``ticker, score, rank, premium``.

COMPARE (post-hoc)
    Loads two or more snapshot CSVs and reports pairwise Spearman rank
    correlations. Pass if all pairs >= 0.85.

LOOP (live, blocks)
    Schedules N captures at fixed minute intervals, then auto-compares.
    Run this from a terminal at market open and walk away.

The script intentionally uses the same per-symbol entry point production uses,
so the result reflects the *actual* live screener -- not a reimplementation.

Usage
-----

# One-shot capture (run from cron / scheduled)
    cd backend
    .\venv\Scripts\python.exe ..\scripts\csp_rank_stability.py capture \
        --tag morning --tickers NVDA,PLTR,AAPL,MSFT,AMD,AVGO,...

# Auto loop -- capture every 90 min, 4 times, then compare
    .\venv\Scripts\python.exe ..\scripts\csp_rank_stability.py loop \
        --interval-min 90 --captures 4 --limit 40

# Post-hoc compare existing CSVs
    .\venv\Scripts\python.exe ..\scripts\csp_rank_stability.py compare \
        rank_snapshot_*.csv

Notes
-----
- Requires the network: this hits yfinance via the production data + options
  services. Don't run inside the test suite.
- Score values change with each scan because IV / mid / OI are live snapshots.
  We test *rank* stability, not absolute-score stability, because that is what
  the production cutoff acts on.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# Force UTF-8 stdout (Windows cp1252 otherwise crashes on Unicode in scoring details).
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

# Make `services.*` imports work when run as `python scripts/csp_rank_stability.py ...`
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

import pandas as pd  # noqa: E402

from services.csp_service import process_symbol  # noqa: E402
from services.universe import MOMENTUM_UNIVERSE  # noqa: E402

logger = logging.getLogger("csp_rank_stability")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SPEARMAN_PASS = 0.85


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

def _scan_universe(tickers: list[str], min_dte: int, max_dte: int) -> pd.DataFrame:
    """Run process_symbol per ticker, take the best strike per ticker, return a DataFrame."""
    rows: list[dict] = []
    for i, t in enumerate(tickers, 1):
        try:
            results, err = process_symbol(t, min_dte=min_dte, max_dte=max_dte)
            if err is not None or not results:
                continue
            # results is list[CspResult] (one per expiration). Take the global best strike.
            best_score = -1.0
            best_row: dict | None = None
            for r in results:
                for strike in r.strikes:
                    if strike.csp_score > best_score:
                        best_score = strike.csp_score
                        best_row = {
                            "ticker": t,
                            "score": strike.csp_score,
                            "env_score": strike.env_score,
                            "strike_score": strike.strike_score,
                            "strike": strike.strike,
                            "delta": strike.delta,
                            "premium": strike.premium,
                            "annualized_return": strike.annualized_return,
                            "dte": r.dte,
                            "expiration": r.expiration,
                            "spot": r.price,
                        }
            if best_row is not None:
                rows.append(best_row)
        except Exception as exc:  # noqa: BLE001
            logger.warning("scan failed for %s: %s", t, exc)
        if i % 10 == 0:
            logger.info("  scanned %d / %d (kept %d)", i, len(tickers), len(rows))

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("score", ascending=False).reset_index(drop=True)
        df["rank"] = df.index + 1
    return df


def capture(args: argparse.Namespace) -> None:
    tickers = _resolve_tickers(args)
    logger.info("Capturing rank snapshot for %d tickers", len(tickers))
    df = _scan_universe(tickers, args.min_dte, args.max_dte)
    if df.empty:
        sys.exit("No results returned. Market may be closed or universe filters too tight.")

    ts = datetime.now()
    tag = args.tag or ts.strftime("%H%M")
    out = Path(args.out_dir) / f"rank_snapshot_{ts.strftime('%Y%m%d')}_{tag}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"\nWrote {len(df)} ranked tickers to {out}")
    print(df[["rank", "ticker", "score", "strike", "delta", "annualized_return"]].head(15).to_string(index=False))
    print(f"\n  Mean score: {df['score'].mean():.1f}    above-65: {(df['score'] >= 65).sum()} / {len(df)}")


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

def _spearman(a: pd.Series, b: pd.Series) -> float:
    """Pearson on rank-transformed values = Spearman."""
    from scipy.stats import spearmanr  # type: ignore
    rho, _ = spearmanr(a, b)
    return float(rho)


def compare(args: argparse.Namespace) -> None:
    paths = sorted(Path(".").glob(args.pattern) if "*" in args.pattern else [Path(p) for p in args.snapshots])
    if not args.snapshots and "*" not in (args.pattern or ""):
        sys.exit("Pass at least 2 snapshot files or a --pattern glob.")
    if args.snapshots:
        paths = sorted(Path(p) for p in args.snapshots)
    if len(paths) < 2:
        sys.exit(f"Need at least 2 snapshots; got {len(paths)}.")

    snaps: dict[str, pd.DataFrame] = {}
    for p in paths:
        df = pd.read_csv(p)
        if df.empty or "ticker" not in df.columns or "score" not in df.columns:
            logger.warning("skipping malformed snapshot %s", p)
            continue
        snaps[p.stem] = df.set_index("ticker")

    if len(snaps) < 2:
        sys.exit("Need at least 2 valid snapshots.")

    print(f"\nLoaded {len(snaps)} snapshots:")
    for name, df in snaps.items():
        print(f"  {name}: {len(df)} tickers   above-65: {(df['score'] >= 65).sum()}")
    print()

    # Intersect on common tickers
    common = set.intersection(*(set(df.index) for df in snaps.values()))
    if len(common) < 5:
        sys.exit(f"Only {len(common)} tickers common to all snapshots -- can't compute stable rho.")
    print(f"Common tickers (intersection): {len(common)}\n")

    names = list(snaps.keys())
    print("=" * 78)
    print("PAIRWISE SPEARMAN RANK CORRELATION (score-based)")
    print("=" * 78)
    header = f"{'':>30}" + "".join(f"{n[-12:]:>14}" for n in names)
    print(header)
    breaches: list[tuple[str, str, float]] = []
    for ni in names:
        row = f"{ni[-30:]:>30}"
        for nj in names:
            if ni == nj:
                row += f"{'1.000':>14}"
                continue
            si = snaps[ni].loc[list(common), "score"]
            sj = snaps[nj].loc[list(common), "score"]
            rho = _spearman(si, sj)
            row += f"{rho:+14.3f}"
            if rho < SPEARMAN_PASS and ni < nj:
                breaches.append((ni, nj, rho))
        print(row)

    # Top-N stability: do the same top-K names appear across snapshots?
    print()
    print("=" * 78)
    print(f"TOP-10 STABILITY (Jaccard on top-10 tickers between successive snapshots)")
    print("=" * 78)
    for i in range(len(names) - 1):
        a = set(snaps[names[i]].sort_values("score", ascending=False).head(10).index)
        b = set(snaps[names[i + 1]].sort_values("score", ascending=False).head(10).index)
        j = len(a & b) / len(a | b) if (a | b) else 0.0
        print(f"  {names[i][-30:]:>30}  ->  {names[i + 1][-30:]:>30}   Jaccard = {j:.2f}   common = {sorted(a & b)}")

    # Verdict
    print()
    print("=" * 78)
    print("VERDICT")
    print("=" * 78)
    if not breaches:
        print(f"  PASS -- all pairwise Spearman correlations >= {SPEARMAN_PASS}")
        print("          intraday ranks are stable; the 65-cutoff sorts on signal, not noise.")
    else:
        print(f"  FAIL -- {len(breaches)} pair(s) below the audit threshold of {SPEARMAN_PASS}:")
        for a, b, r in breaches:
            print(f"    {a}  vs  {b}:   rho = {r:+.3f}")
        print("          Production cutoff is partially driven by intra-day noise. Consider:")
        print("            - smoothing scores over a rolling window")
        print("            - widening cutoff to a band (e.g., '65 +- 3 = soft')")
        print("            - logging score drift across the day for the same ticker")


# ---------------------------------------------------------------------------
# Loop (capture N times, then compare)
# ---------------------------------------------------------------------------

def loop(args: argparse.Namespace) -> None:
    tickers = _resolve_tickers(args)
    logger.info("Loop mode: %d captures, %d-min interval, %d tickers each",
                args.captures, args.interval_min, len(tickers))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for i in range(args.captures):
        ts = datetime.now()
        tag = f"capture{i + 1}_{ts.strftime('%H%M')}"
        df = _scan_universe(tickers, args.min_dte, args.max_dte)
        if df.empty:
            logger.error("Empty result at %s; skipping", tag)
        else:
            out = out_dir / f"rank_snapshot_{ts.strftime('%Y%m%d')}_{tag}.csv"
            df.to_csv(out, index=False)
            paths.append(out)
            logger.info("Wrote snapshot %d -> %s (%d tickers)", i + 1, out, len(df))

        if i < args.captures - 1:
            logger.info("Sleeping %d min before next capture...", args.interval_min)
            time.sleep(args.interval_min * 60)

    # Auto-compare
    if len(paths) >= 2:
        args.snapshots = [str(p) for p in paths]
        args.pattern = ""
        compare(args)
    else:
        logger.error("Only %d snapshot(s) captured -- nothing to compare.", len(paths))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_tickers(args: argparse.Namespace) -> list[str]:
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = list(MOMENTUM_UNIVERSE)
    if args.limit:
        tickers = tickers[: args.limit]
    return tickers


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--tickers", type=str, default=None,
                        help="Comma-separated tickers (default: full MOMENTUM_UNIVERSE)")
    common.add_argument("--limit", type=int, default=None,
                        help="Take the first N tickers from the universe")
    common.add_argument("--min-dte", type=int, default=30)
    common.add_argument("--max-dte", type=int, default=60)
    common.add_argument("--out-dir", type=str, default=".")

    cap = sub.add_parser("capture", parents=[common], help="One-shot capture")
    cap.add_argument("--tag", type=str, default=None,
                     help="Label appended to filename (default: HHMM)")
    cap.set_defaults(func=capture)

    cmp = sub.add_parser("compare", parents=[common], help="Compare existing snapshots")
    cmp.add_argument("snapshots", nargs="*", help="Snapshot CSVs to compare")
    cmp.add_argument("--pattern", type=str, default="",
                     help="Glob pattern instead of explicit list (e.g. 'rank_snapshot_*.csv')")
    cmp.set_defaults(func=compare)

    lp = sub.add_parser("loop", parents=[common], help="Capture N times then auto-compare")
    lp.add_argument("--captures", type=int, default=4,
                    help="Number of snapshots to capture")
    lp.add_argument("--interval-min", type=int, default=90,
                    help="Minutes between captures")
    lp.set_defaults(func=loop)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
