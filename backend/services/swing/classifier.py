"""
Setup detection + classification for the swing screener.

Four setup types, each scored 0–100. The setup with the highest score is
returned as `best_setup`. Each setup has its own narrative and risk geometry.

  Breakout       (5–10d hold)  : consolidation + volume surge + structure reclaim
  Momentum       (7–14d hold)  : EMA alignment + ADX trend + RS leadership
  Reversion      (3–7d hold)   : oversold bounce with positive divergence
  Retest         (10–21d hold) : prior breakout level held + base re-formed

Each detector returns a score 0–100 and a list of "drivers" (short phrases
explaining what triggered).
"""
from __future__ import annotations

from typing import Any


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def detect_breakout(f: dict[str, Any]) -> dict:
    """
    Breakout setup — price compressing into a tight base then expanding on volume.

    Strong drivers:
      - consolidation base ≥ 7d, range ≤ 8%
      - volume surge (today's volume ≥ 1.5× 20d avg)
      - structure-high reclaim within last 5 bars
      - BB squeeze percentile < 25 (tight bands releasing)
    """
    score = 0.0
    drivers: list[str] = []

    base = f.get("consolidation_base") or {}
    if base.get("is_base"):
        score += 25
        drivers.append(f"tight base {base.get('days')}d / {base.get('range_pct', 0) * 100:.1f}% range")

    surge = f.get("volume_surge") or {}
    if surge.get("is_surge"):
        score += 20
        drivers.append(f"volume {surge.get('ratio', 0):.2f}× avg")

    reclaim = f.get("structure_reclaim") or {}
    if reclaim.get("reclaimed"):
        score += 25
        drivers.append(f"reclaimed structure high @ {reclaim.get('level')}")

    bb_pct = f.get("bb_squeeze_pct")
    if bb_pct is not None and bb_pct == bb_pct and bb_pct < 25:
        score += 15
        drivers.append(f"BB squeeze {bb_pct:.0f}p")

    # Trend confirmation bonus
    ema = f.get("ema_alignment") or {}
    if ema.get("score", 0) >= 6:
        score += 15
        drivers.append("EMA stack aligned")

    # Within-setup multiplier: a breakout without volume is structurally
    # different from a breakout with volume (most retail breakouts fail because
    # of this). Halve the score floor at 0.5; never zero (keeps row debuggable).
    surge_ratio = (surge.get("ratio") or 0.0) if isinstance(surge.get("ratio"), (int, float)) else 0.0
    vol_factor = max(0.5, min(1.0, surge_ratio / 1.5)) if surge_ratio > 0 else 0.5
    score *= vol_factor

    return {"score": _clamp(score), "drivers": drivers}


def detect_momentum(f: dict[str, Any]) -> dict:
    """
    Momentum setup — trending name with leadership; ride continuation.

    Strong drivers:
      - EMA alignment ≥ 7 (3 of 4 above plus bonus)
      - ADX ≥ 22 with +DI > -DI
      - RS vs SPY > 1.1
      - MACD histogram inflection (recent zero-cross up)
    """
    score = 0.0
    drivers: list[str] = []

    ema = f.get("ema_alignment") or {}
    if ema.get("score", 0) >= 7:
        score += 25
        drivers.append(f"EMA alignment {ema.get('score')}/9")
    elif ema.get("score", 0) >= 5:
        score += 12

    adx = f.get("adx") or {}
    adx_val = adx.get("adx") or 0
    if adx_val and adx_val == adx_val and adx_val >= 22 and (adx.get("plus_di") or 0) > (adx.get("minus_di") or 0):
        score += 25
        drivers.append(f"ADX {adx_val:.0f} +DI dominant")
    elif adx_val >= 18:
        score += 12

    rs = f.get("rs_vs_spy")
    if rs is not None and rs == rs and rs > 1.1:
        score += 20
        drivers.append(f"RS vs SPY {rs:.2f}")
    elif rs is not None and rs > 1.0:
        score += 10

    if f.get("macd_inflection"):
        score += 15
        drivers.append("MACD hist crossed zero")

    # higher lows confirmation
    hl = f.get("higher_lows", 0)
    if hl >= 2:
        score += 15
        drivers.append(f"{hl} consecutive higher lows")
    elif hl >= 1:
        score += 7

    # Within-setup multiplier: momentum without an aligned EMA stack is just
    # a price spike. Floor at 0.6 so the row still appears for debugging.
    ema_score = ema.get("score", 0) or 0
    align_factor = max(0.6, min(1.0, ema_score / 7.0)) if ema_score > 0 else 0.6
    score *= align_factor

    return {"score": _clamp(score), "drivers": drivers}


def detect_reversion(f: dict[str, Any]) -> dict:
    """
    Reversion setup — oversold bounce in a name with intact structure.

    Strong drivers:
      - RSI < 35
      - Stochastic %K < 20
      - Bullish RSI divergence (price LL, RSI HL)
      - Fib 0.618 hold on most recent swing
    """
    score = 0.0
    drivers: list[str] = []

    rsi = f.get("rsi")
    if rsi is not None and rsi == rsi:
        if rsi < 30:
            score += 25
            drivers.append(f"RSI {rsi:.0f} oversold")
        elif rsi < 40:
            score += 12

    stoch = f.get("stochastic") or {}
    k = stoch.get("k")
    if k is not None and k == k and k < 20:
        score += 20
        drivers.append(f"stoch %K {k:.0f}")

    if f.get("rsi_divergence"):
        score += 25
        drivers.append("bullish RSI divergence")

    if f.get("fib_618_hold"):
        score += 15
        drivers.append("holding 0.618 fib")

    # Higher-timeframe context: must still be above 200 EMA for clean reversion.
    # Guard against NaN: ema200 from compute_ema_alignment is float("nan") when there
    # are fewer than 200 bars. `if nan` is truthy in Python, but `x > nan` is False,
    # so the reward branch silently never fires — but the block branch also silently
    # never fires (NaN < x = False), letting falling-knife reversions through on new
    # listings. Explicit NaN check (x == x is False for NaN) fixes both branches.
    ema = f.get("ema_alignment") or {}
    ema200 = ema.get("ema200")
    _ema200_valid = ema200 is not None and ema200 == ema200 and float(ema200) > 0
    price = float(f.get("price") or 0)
    if _ema200_valid and price > float(ema200):
        score += 15
        drivers.append("above 200 EMA")

    # Hard floor: reversion below the 200 EMA is catching falling knives.
    # Zero out the setup so the runner's MIN_SETUP_SCORE gate excludes the row.
    if _ema200_valid and price < float(ema200):
        return {"score": 0.0, "drivers": drivers + ["below 200 EMA — reversion blocked"]}

    return {"score": _clamp(score), "drivers": drivers}


def detect_retest(f: dict[str, Any]) -> dict:
    """
    Retest setup — prior breakout level is being re-tested and holding;
    base is reforming for a second leg.

    Strong drivers:
      - structure_reclaim happened earlier (bars_since_reclaim 6–20)
      - new consolidation_base forming above the reclaimed level
      - RS vs SPY ≥ 1.0 (not breaking down on relative basis)
    """
    score = 0.0
    drivers: list[str] = []

    reclaim = f.get("structure_reclaim") or {}
    bars_since = reclaim.get("bars_since_reclaim", -1)
    if reclaim.get("reclaimed") and 5 <= bars_since <= 20:
        score += 30
        drivers.append(f"reclaimed {bars_since}d ago, retesting")

    base = f.get("consolidation_base") or {}
    if base.get("is_base") and base.get("days", 0) >= 5:
        score += 25
        drivers.append(f"re-base {base.get('days')}d")

    rs = f.get("rs_vs_spy")
    if rs is not None and rs == rs and rs >= 1.0:
        score += 15
        drivers.append(f"RS holding {rs:.2f}")

    # Gap-fill candidate adds confluence
    gap = f.get("gap_fill") or {}
    if gap.get("has_gap"):
        score += 15
        drivers.append(f"gap @ {gap.get('gap_level')} ({gap.get('distance_pct'):.1f}%)")

    # EMA stack still healthy
    ema = f.get("ema_alignment") or {}
    if ema.get("score", 0) >= 6:
        score += 15
        drivers.append("EMA stack intact")

    # Within-setup multiplier: retest only works inside the 5–20 bar window.
    # Outside that window the thesis is stale; halve the score.
    bars = reclaim.get("bars_since_reclaim", -1) if isinstance(reclaim, dict) else -1
    if bars < 0 or bars < 5 or bars > 20:
        score *= 0.5

    return {"score": _clamp(score), "drivers": drivers}


def classify_setup(features: dict[str, Any]) -> dict:
    """
    Run all four detectors. Return the winner plus all scores.

    Returns:
      best_setup    : str  ("breakout" | "momentum" | "reversion" | "retest")
      best_score    : float
      scores        : dict[str, float]
      drivers       : list[str]  (winner's drivers)
      all_drivers   : dict[str, list[str]]
    """
    results = {
        "breakout": detect_breakout(features),
        "momentum": detect_momentum(features),
        "reversion": detect_reversion(features),
        "retest": detect_retest(features),
    }
    scores = {name: r["score"] for name, r in results.items()}
    best_name = max(scores, key=lambda k: scores[k])
    return {
        "best_setup": best_name,
        "best_score": scores[best_name],
        "scores": scores,
        "drivers": results[best_name]["drivers"],
        "all_drivers": {name: r["drivers"] for name, r in results.items()},
    }
