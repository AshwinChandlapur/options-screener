"""
Scoring weight tables — DOCUMENTATION ONLY (for now).

These dicts describe *what* the screener scorers are supposed to weight.
They do **not** drive the math: `env.py` and `strike.py` currently hardcode
their per-factor caps (e.g. `p = 22.0` for HV Rank) inside the bell-curve
branches. The dicts are kept here so that:

- `SCORING_REFERENCE.md` and the frontend `SCORE_LEGEND` arrays have a single
  textual source-of-truth to reference,
- a future Phase (see ADR-0001) can either parameterize the scorers to actually
  consume these dicts, or formally retire them.

DO NOT change a value here expecting screener output to change — it won't.
Either update the matching constant in the scorer body too, or wire up the
parameterization first.

Per copilot-instructions.md: any tweak to these weights requires an ADR plus
matching updates to `SCORING_REFERENCE.md` and the frontend legend.
"""
from __future__ import annotations

# Environment-score factor weights (CSP/CC). Sum = 100.
# Mirror of the per-factor caps inside `compute_env_score` in `env.py`.
ENV_WEIGHTS: dict[str, float] = {
    'HV':  22.0,   # HV Rank (uses 30d HV proxy, named iv_rank for back-compat)
    'IH':  28.0,   # IV / HV Ratio
    'SMA': 15.0,   # SMA Alignment
    '52W': 10.0,   # 52W High Distance (direction-aware)
    'RSI': 10.0,   # RSI (direction-aware)
    'OI':   8.0,   # Chain Median OI (circuit breaker)
    'DTE':  7.0,   # DTE sweet spot
}
ENV_MAX: float = sum(ENV_WEIGHTS.values())  # 100.0

# Earnings-within-DTE penalty applied on top of the env score.
# Mirror of the constant used by `compute_env_score`.
EARNINGS_PENALTY: float = -15.0

# Strike-score factor weights (CSP/CC). Sum = 100.
# Note: the breakdown keys emitted by the scorers are 'Sup' (CSP) / 'Res' (CC)
# rather than the abstract 'SR' used here. The naming will be reconciled when
# the dict is actually consumed (ADR-0001).
STRIKE_WEIGHTS: dict[str, float] = {
    'Δ':   15.0,   # Delta bell-curve
    'SR':  18.0,   # Distance vs Support / Resistance
    'EM':  20.0,   # Expected Move Buffer
    'OTM':  9.0,   # % OTM from spot
    'BA':  23.0,   # Bid-Ask spread
    'LQ':   5.0,   # OI / Volume circuit breaker
    'ROC': 10.0,   # Annualized return on capital
}
STRIKE_MAX: float = sum(STRIKE_WEIGHTS.values())  # 100.0
