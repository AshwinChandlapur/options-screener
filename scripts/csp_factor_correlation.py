r"""
CSP factor correlation analysis -- validates / falsifies audit finding HIGH-4.

The audit claims the CSP ENV score has *triple-counted* trend exposure: Tr (15)
+ SMA (5) + SLP (5) all measure the same regime variable, and the audit
predicted pairwise correlations >= 0.6 within the {Tr, SMA, SLP} cluster, plus
co-movement with RSI in sustained trends.

This script tests that empirically using the ledger emitted by
``scripts/backtest_csp.py --out <csv>``:

  1. Loads the per-trade ledger.
  2. Computes the Pearson correlation matrix of per-factor sub-scores
     (env_IVP, env_Tr, env_SMA, env_SLP, env_RSI, env_OI,
      strike_Delta, strike_ROC).
  3. Highlights the trend cluster {Tr, SMA, SLP} pairwise correlations.
  4. Renders a text-mode correlation heatmap.
  5. Reports the verdict against the audit threshold (>= 0.6 = HIGH-4 confirmed).

Usage:
    .\venv\Scripts\python.exe ..\scripts\csp_factor_correlation.py csp_backtest_full.csv
    .\venv\Scripts\python.exe ..\scripts\csp_factor_correlation.py csp_backtest_full.csv --out corr.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Force UTF-8 stdout so Unicode in summaries doesn't crash on Windows cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

import numpy as np
import pandas as pd

FACTOR_COLS = [
    "env_IVP", "env_Tr", "env_SMA", "env_SLP", "env_RSI", "env_OI",
    "strike_Delta", "strike_ROC",
]
TREND_CLUSTER = ["env_Tr", "env_SMA", "env_SLP"]
AUDIT_THRESHOLD = 0.6


def _heatmap(corr: pd.DataFrame) -> str:
    """Tiny text heatmap. Cells -> {., -, =, #, @} by abs(corr) magnitude."""
    def cell(v: float) -> str:
        a = abs(v)
        ch = "@" if a >= 0.8 else "#" if a >= 0.6 else "=" if a >= 0.4 else "-" if a >= 0.2 else "."
        sign = "+" if v >= 0 else "-"
        return f" {sign}{ch} "
    cols = list(corr.columns)
    header = "          " + "".join(f"{c:>9}" for c in cols)
    lines = [header]
    for r in cols:
        row = f"{r:>10}" + "".join(cell(corr.loc[r, c]) for c in cols)
        lines.append(row)
    legend = "\nLegend: cells are [sign][magnitude]: . = |r|<.2  - = .2-.4  = = .4-.6  # = .6-.8  @ = >=.8"
    return "\n".join(lines) + legend


def analyze(ledger_path: Path, out_path: Path | None) -> None:
    df = pd.read_csv(ledger_path)
    missing = [c for c in FACTOR_COLS if c not in df.columns]
    if missing:
        sys.exit(
            f"Ledger {ledger_path} is missing factor columns {missing}. "
            "Re-run backtest_csp.py with the latest version (sub-scores were added in Phase 2)."
        )
    print(f"\nLoaded {len(df):,} trades from {ledger_path}")
    print(f"  Date range: {df['scan_date'].min()} -> {df['scan_date'].max()}")
    print(f"  Tickers:    {df['ticker'].nunique()}")
    print()

    factors = df[FACTOR_COLS].copy()
    corr = factors.corr(method="pearson")

    print("=" * 78)
    print("PEARSON CORRELATION MATRIX -- per-factor sub-scores")
    print("=" * 78)
    print(corr.round(2).to_string())
    print()
    print(_heatmap(corr))
    print()

    # Trend-cluster verdict
    print("=" * 78)
    print("AUDIT HIGH-4 TEST -- 'Tr + SMA + SLP triple-count trend'")
    print("=" * 78)
    pairs = [
        ("env_Tr",  "env_SMA"),
        ("env_Tr",  "env_SLP"),
        ("env_SMA", "env_SLP"),
    ]
    breaches = 0
    for a, b in pairs:
        r = corr.loc[a, b]
        flag = "  <-- BREACHES AUDIT THRESHOLD" if abs(r) >= AUDIT_THRESHOLD else ""
        if abs(r) >= AUDIT_THRESHOLD:
            breaches += 1
        print(f"  corr({a:<8}, {b:<8}) = {r:+.3f}{flag}")
    print()
    if breaches == 0:
        print(f"  VERDICT: AUDIT WRONG on this sample -- no trend-cluster pair exceeds |r| >= {AUDIT_THRESHOLD}.")
        print("           The three trend factors carry materially independent variance.")
    elif breaches == 3:
        print(f"  VERDICT: AUDIT CONFIRMED -- all three trend-cluster pairs exceed |r| >= {AUDIT_THRESHOLD}.")
        print("           Collapse {Tr, SMA, SLP} into a single 25-pt trend bundle per audit remediation.")
    else:
        print(f"  VERDICT: PARTIAL -- {breaches}/3 trend-cluster pairs exceed |r| >= {AUDIT_THRESHOLD}.")
        print("           The trend cluster is partially redundant; review case-by-case.")

    # Trend vs RSI
    print()
    print("Trend cluster vs RSI(14) -- audit predicted co-movement in sustained trends:")
    for t in TREND_CLUSTER:
        r = corr.loc[t, "env_RSI"]
        print(f"  corr({t:<8}, env_RSI ) = {r:+.3f}")

    # IV factor independence
    print()
    print("IV-percentile independence vs trend / momentum factors:")
    for t in TREND_CLUSTER + ["env_RSI"]:
        r = corr.loc["env_IVP", t]
        print(f"  corr(env_IVP , {t:<8}) = {r:+.3f}")

    # Strike-side internal correlation
    print()
    print("Strike-side factors (Delta + ROC):")
    r = corr.loc["strike_Delta", "strike_ROC"]
    print(f"  corr(strike_Delta, strike_ROC) = {r:+.3f}")
    if abs(r) >= AUDIT_THRESHOLD:
        print("  NOTE: Delta and ROC are highly correlated by construction (low delta -> low premium -> low ROC).")
        print("        This is expected; the strike score is intentionally two coupled views of strike richness.")

    if out_path:
        corr.to_csv(out_path)
        print(f"\nWrote correlation matrix to {out_path}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("ledger", type=Path, help="CSV emitted by backtest_csp.py --out")
    ap.add_argument("--out", type=Path, default=None, help="Optional path to write the correlation matrix as CSV")
    args = ap.parse_args()

    if not args.ledger.exists():
        sys.exit(f"Ledger file not found: {args.ledger}")
    analyze(args.ledger, args.out)


if __name__ == "__main__":
    main()
