"""
Scoring package — pure functions and constants that turn indicator values into
0–100 scores for the CSP and CC screeners.

Structure:
- `config.py`  — weight constants. Documentation-only at the moment: the curves
                 in `env.py` / `strike.py` still hardcode their own caps. ADR-0001
                 will decide whether to parameterize them.
- `env.py`     — `compute_env_score` (CSP/CC, direction-aware).
- `strike.py`  — CSP/CC strike-quality scorers + final-blend helpers.

DITM is deliberately not here yet: its scorers still live inline in
`ditm_service.py` (Phase 4 will move them in via the `ScreenerService`
refactor — see ADR-0002).

These modules are I/O-free: no FastAPI, no yfinance, no DB. They take primitives
in and return primitives / dataclasses out, which keeps them easy to unit-test
and reuse from the upcoming `ScreenerService`.
"""
