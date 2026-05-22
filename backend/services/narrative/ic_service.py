"""IC monitor service — reads ic_snapshots from Cosmos and computes rolling IC.

Produces a week-by-week Spearman IC series for the live 90-day validation
window.  Read-only — never writes to Cosmos.

Spearman IC = Spearman rank correlation between ACS at snapshot_date and
forward_return_pct over the following FORWARD_DAYS calendar days.

Why Spearman:
  Rank correlation is robust to outliers and does not assume linearity.
  It answers: "do higher-ranked tickers by ACS tend to have higher returns?"
  which is the operationally correct question for a screener.

Reporting buckets:
  Snapshots are grouped by ISO week of snapshot_date.
  A cohort is reported only when it has >= MIN_COHORT_FOR_IC complete pairs.
  The cumulative IC (all complete pairs to date) is also reported.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)

MIN_COHORT_FOR_IC: int = 10   # mirror of ic_snapshot.py
MIN_ASYMMETRY_N: int = 20     # minimum complete pairs to report distribution stats

# All factors stored in the snapshot that will be tested for IC.
# Listed in intended display order.  "acs" is the primary hypothesis;
# the rest are secondary diagnostic factors.
_IC_FACTORS: list[str] = [
    "acs",               # total score (primary)
    "acs_raw",           # pre-haircut sum of A+B+C+D
    "decay_acs",         # time-decayed ACS
    "comp_a",            # Component A: attention persistence
    "comp_b",            # Component B: contributor quality
    "comp_c",            # Component C: narrative strength
    "comp_d",            # Component D: thesis quality
    "dwd_14d",           # A raw input: decay-weighted density
    "gini_14d",          # B/haircut raw: Gini coefficient (negative IC expected)
    "stage_confidence",  # C raw: detector confidence
    "s_br",              # D raw: bull-researched joint share
    "s_Br",              # D raw: bear-researched joint share
    "acs_slope_14d",     # continuity: 14-day ACS momentum
    "stage_streak_days", # continuity: consecutive days in stage 1-3
]

# Module-level lazy client.
_client: CosmosClient | None = None
_ic_container = None  # type: ignore[assignment]


def _get_ic_container():  # type: ignore[return]
    global _client, _ic_container
    if _ic_container is None:
        endpoint = os.getenv("NARRATIVE_COSMOS_ENDPOINT") or os.getenv("COSMOS_ENDPOINT", "")
        if not endpoint:
            raise RuntimeError("NARRATIVE_COSMOS_ENDPOINT not set")
        if _client is None:
            _client = CosmosClient(endpoint, credential=DefaultAzureCredential())
        db = os.getenv("NARRATIVE_COSMOS_DB") or os.getenv("COSMOS_DB", "narrative")
        _ic_container = _client.get_database_client(db).get_container_client("ic_snapshots")
    return _ic_container


@dataclass
class FactorIc:
    """IC stats for a single factor vs forward_return_pct."""
    factor: str
    n: int              # number of complete pairs with this factor present
    ic: float | None    # Spearman rho; None if n < MIN_COHORT_FOR_IC
    p_value: float | None


@dataclass
class AsymmetryBucket:
    """Return distribution stats for one ACS / stage segment.

    The asymmetry question: do high-ACS or Emerging tickers have a
    right-skewed return distribution (many small losses + occasional large
    gains) vs the baseline?

    Populated only when n >= MIN_ASYMMETRY_N; all stats are None below that.
    """
    label: str              # e.g. "ACS top quartile", "Emerging (stage 2–3)"
    n: int
    mean_ret: float | None
    median_ret: float | None
    std_ret: float | None
    skewness: float | None  # > 0 = right-skewed (fat right tail)
    win_rate: float | None  # fraction of returns > 0
    upside_10: float | None  # fraction of returns > +10%
    downside_10: float | None  # fraction of returns < −10%
    tail_ratio: float | None  # upside_10 / downside_10 ; >1 = asymmetric right


@dataclass
class WeeklyIcPoint:
    """IC value for a single ISO-week cohort of complete snapshots."""
    week_label: str      # "2026-W22" — ISO year + week number
    n_pairs: int         # number of complete (ACS, return) pairs in this cohort
    ic: float | None     # Spearman IC; None if cohort too small
    p_value: float | None
    mean_acs: float
    mean_return_pct: float


@dataclass
class IcMonitorReport:
    forward_days: int
    cumulative_ic: float | None       # Spearman over all complete pairs
    cumulative_n: int
    cumulative_p_value: float | None
    weekly: list[WeeklyIcPoint]
    factor_ics: list[FactorIc]        # per-factor IC across all complete pairs
    asymmetry: list[AsymmetryBucket]  # return distribution by ACS/stage segment
    total_snapshots: int              # all snapshots including incomplete
    total_complete: int
    pct_complete: float               # fraction of snapshots that have returns
    window_start: str | None          # earliest snapshot_date in corpus
    window_end: str | None            # latest snapshot_date in corpus
    last_computed_at: str


def get_ic_report() -> IcMonitorReport:
    """Fetch all IC snapshot data and compute the rolling IC report."""
    container = _get_ic_container()

    all_docs = list(
        container.query_items(
            query="SELECT * FROM c ORDER BY c.snapshot_date ASC",
            enable_cross_partition_query=True,
        )
    )

    total = len(all_docs)
    complete = [d for d in all_docs if d.get("is_complete")]
    n_complete = len(complete)
    pct_complete = n_complete / total if total > 0 else 0.0

    dates = [d["snapshot_date"] for d in all_docs if "snapshot_date" in d]
    window_start = min(dates) if dates else None
    window_end = max(dates) if dates else None

    cumulative_ic, cumulative_p = _spearman_ic(complete) if n_complete >= MIN_COHORT_FOR_IC else (None, None)

    # Group complete snapshots by ISO week.
    from collections import defaultdict
    by_week: dict[str, list[dict]] = defaultdict(list)
    for doc in complete:
        try:
            d = date.fromisoformat(doc["snapshot_date"])
            label = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
            by_week[label].append(doc)
        except (KeyError, ValueError):
            continue

    weekly: list[WeeklyIcPoint] = []
    for week_label in sorted(by_week):
        cohort = by_week[week_label]
        ic, p = _spearman_ic(cohort) if len(cohort) >= MIN_COHORT_FOR_IC else (None, None)
        acss = [d["acs"] for d in cohort]
        rets = [d["forward_return_pct"] for d in cohort]
        weekly.append(WeeklyIcPoint(
            week_label=week_label,
            n_pairs=len(cohort),
            ic=round(ic, 4) if ic is not None else None,
            p_value=round(p, 4) if p is not None else None,
            mean_acs=round(sum(acss) / len(acss), 2),
            mean_return_pct=round(sum(rets) / len(rets), 2),
        ))

    return IcMonitorReport(
        forward_days=30,
        cumulative_ic=round(cumulative_ic, 4) if cumulative_ic is not None else None,
        cumulative_n=n_complete,
        cumulative_p_value=round(cumulative_p, 4) if cumulative_p is not None else None,
        weekly=weekly,
        factor_ics=_compute_factor_ics(complete),
        asymmetry=_compute_asymmetry(complete),
        total_snapshots=total,
        total_complete=n_complete,
        pct_complete=round(pct_complete, 4),
        window_start=window_start,
        window_end=window_end,
        last_computed_at=datetime.now(tz=timezone.utc).isoformat(),
    )


def _spearman_ic(docs: list[dict]) -> tuple[float, float]:
    """Return (spearman_rho, p_value) for the given complete snapshot docs."""
    try:
        from scipy.stats import spearmanr  # noqa: PLC0415
    except ImportError:
        raise RuntimeError("scipy not installed; cannot compute Spearman IC")

    acss = [float(d["acs"]) for d in docs]
    rets = [float(d["forward_return_pct"]) for d in docs]
    if len(acss) < 2:
        return 0.0, 1.0
    result = spearmanr(acss, rets)
    return float(result.statistic), float(result.pvalue)


def _compute_factor_ics(complete: list[dict]) -> list[FactorIc]:
    """Compute Spearman IC for each factor in _IC_FACTORS vs forward_return_pct."""
    try:
        from scipy.stats import spearmanr  # noqa: PLC0415
    except ImportError:
        return []

    result: list[FactorIc] = []
    for factor in _IC_FACTORS:
        pairs = [
            (float(d[factor]), float(d["forward_return_pct"]))
            for d in complete
            if d.get(factor) is not None and d.get("forward_return_pct") is not None
        ]
        n = len(pairs)
        if n < MIN_COHORT_FOR_IC:
            result.append(FactorIc(factor=factor, n=n, ic=None, p_value=None))
            continue
        xs, ys = zip(*pairs)
        sr = spearmanr(list(xs), list(ys))
        result.append(FactorIc(
            factor=factor,
            n=n,
            ic=round(float(sr.statistic), 4),
            p_value=round(float(sr.pvalue), 4),
        ))
    return result


def _compute_asymmetry(complete: list[dict]) -> list[AsymmetryBucket]:
    """Compute return distribution stats for ACS/stage segments.

    Segments:
      - All complete pairs (baseline)
      - ACS top quartile  (≥ 75th pct of ACS in corpus)
      - ACS bottom quartile (≤ 25th pct)
      - Emerging — lifecycle_stage in {2, 3}
      - Non-emerging
      - Cold-start — lifecycle_stage == 0

    Requires numpy + scipy.  Returns empty list if not installed.
    """
    if not complete:
        return []
    try:
        import numpy as np
        from scipy.stats import skew as scipy_skew  # noqa: PLC0415
    except ImportError:
        return []

    def _bucket(label: str, rets: list[float]) -> AsymmetryBucket:
        n = len(rets)
        if n < MIN_ASYMMETRY_N:
            return AsymmetryBucket(
                label=label, n=n,
                mean_ret=None, median_ret=None, std_ret=None,
                skewness=None, win_rate=None,
                upside_10=None, downside_10=None, tail_ratio=None,
            )
        arr = np.array(rets, dtype=float)
        up10 = float(np.mean(arr > 10.0))
        dn10 = float(np.mean(arr < -10.0))
        tail = round(up10 / dn10, 3) if dn10 > 0 else None
        return AsymmetryBucket(
            label=label,
            n=n,
            mean_ret=round(float(np.mean(arr)), 2),
            median_ret=round(float(np.median(arr)), 2),
            std_ret=round(float(np.std(arr)), 2),
            skewness=round(float(scipy_skew(arr)), 3),
            win_rate=round(float(np.mean(arr > 0)), 3),
            upside_10=round(up10, 3),
            downside_10=round(dn10, 3),
            tail_ratio=tail,
        )

    all_rets = [float(d["forward_return_pct"]) for d in complete]
    all_acs = [float(d["acs"]) for d in complete]
    import numpy as np  # noqa: PLC0415 (already imported above but needed for percentile)
    q25 = float(np.percentile(all_acs, 25)) if len(all_acs) >= 4 else 0.0
    q75 = float(np.percentile(all_acs, 75)) if len(all_acs) >= 4 else 100.0

    buckets: list[AsymmetryBucket] = [
        _bucket("All", all_rets),
        _bucket(
            "ACS top quartile",
            [float(d["forward_return_pct"]) for d in complete if float(d["acs"]) >= q75],
        ),
        _bucket(
            "ACS bottom quartile",
            [float(d["forward_return_pct"]) for d in complete if float(d["acs"]) <= q25],
        ),
        _bucket(
            "Emerging (stage 2\u20133)",
            [float(d["forward_return_pct"]) for d in complete
             if d.get("lifecycle_stage") in (2, 3)],
        ),
        _bucket(
            "Non-emerging",
            [float(d["forward_return_pct"]) for d in complete
             if d.get("lifecycle_stage") not in (2, 3)],
        ),
        _bucket(
            "Cold-start (stage 0)",
            [float(d["forward_return_pct"]) for d in complete
             if d.get("lifecycle_stage") == 0],
        ),
        _bucket(
            "Small/mid-cap (< $10B)",
            [float(d["forward_return_pct"]) for d in complete
             if d.get("market_cap") is not None and float(d["market_cap"]) < 10_000_000_000],
        ),
        _bucket(
            "Large cap (\u2265 $10B)",
            [float(d["forward_return_pct"]) for d in complete
             if d.get("market_cap") is not None and float(d["market_cap"]) >= 10_000_000_000],
        ),
    ]
    return buckets
