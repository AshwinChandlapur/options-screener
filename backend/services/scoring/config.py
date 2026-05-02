"""
Scoring weight tables — v3 lean model (see ADR-0007).

These dicts describe *what* the screener scorers weight. They do **not**
drive the math: `env.py` and `strike.py` hardcode the per-factor caps
inline. The dicts are the textual source-of-truth referenced by
`SCORING_REFERENCE.md` and the frontend `SCORE_LEGEND` arrays.

Per copilot-instructions.md: any tweak to these weights requires an ADR
plus matching updates to `SCORING_REFERENCE.md` and the frontend legend.

v3 (ADR-0007) reduced the model from 14 factors to 8 to remove
correlated/redundant signals. Dropped: HV Rank, SMA Alignment, DTE Sweet
Spot, EM Buffer, %OTM, S/R Distance. The fields `em_buffer_pct`,
`dist_pct`, and `otm_pct` continue to be computed and returned in the
response payload for diagnostic visibility but contribute 0 to the score.
"""
from __future__ import annotations

# Environment-score factor weights (CSP/CC). Sum = 100.
# Mirror of the per-factor caps inside `compute_env_score` in `env.py`.
ENV_WEIGHTS: dict[str, float] = {
    'IH':  35.0,   # IV / HV Ratio
    'Tr':  25.0,   # Trend (52W high distance, direction-aware)
    'RSI': 20.0,   # RSI(14) (direction-aware)
    'OI':  20.0,   # Chain Median OI (circuit breaker)
}
ENV_MAX: float = sum(ENV_WEIGHTS.values())  # 100.0

# Earnings-within-DTE penalty applied on top of the env score.
EARNINGS_PENALTY: float = -15.0

# Strike-score factor weights (CSP/CC). Sum = 100.
STRIKE_WEIGHTS: dict[str, float] = {
    'Δ':   20.0,   # Delta bell-curve (symmetric)
    'BA':  30.0,   # Bid-Ask spread
    'LQ':  15.0,   # OI / Volume circuit breaker (per-strike)
    'ROC': 35.0,   # Annualized return on capital
}
STRIKE_MAX: float = sum(STRIKE_WEIGHTS.values())  # 100.0
