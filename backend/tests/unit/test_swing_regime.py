"""
Unit tests for the swing market-regime engine.

Covers:
- VIX percentile classification at the 25 / 60 / 85 boundaries.
- Index-trend classification by SPY/EMA21/EMA50 stack.
- Composite multiplier curve (linear in REGIME_MULT_MIN..MAX).
- Disabled-setups list (reversion only in risk_off).
- Graceful degradation: missing SPY → neutral + degraded=True.

All upstream data calls (`get_ohlc` for ^VIX and IWM) are patched. No network.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from services.swing import regime as regime_mod
from services.swing.regime import (
    REGIME_MULT_MAX,
    REGIME_MULT_MIN,
    RR_GATE_BY_REGIME,
    RegimeState,
    _classify_index_trend,
    _classify_vix_regime,
    _label_regime,
    _vix_percentile,
    compute_regime,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_spy(close: float, *, bars: int = 80, drift: float = 0.0) -> pd.DataFrame:
    """Fabricate an SPY OHLC frame ending at `close` with a linear path."""
    start = close - drift * bars
    closes = np.linspace(start, close, bars)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes * 1.005,
            "Low": closes * 0.995,
            "Close": closes,
            "Volume": np.full(bars, 80_000_000, dtype=float),
        }
    )


def _make_vix(latest: float, *, percentile_target: float, bars: int = 252) -> pd.DataFrame:
    """
    Fabricate a 1y VIX frame whose latest bar lands at `percentile_target` of
    the trailing window.
    """
    # Build a simple ramp from 10 → 40, then overwrite the last bar so it lands
    # at the chosen percentile rank.
    series = np.linspace(10.0, 40.0, bars)
    # Position the last value such that exactly percentile_target % of the
    # window are <= latest.
    idx = max(0, min(bars - 1, int(round(percentile_target / 100.0 * bars)) - 1))
    sorted_window = np.sort(series[:-1])
    if idx >= len(sorted_window):
        idx = len(sorted_window) - 1
    latest_value = float(sorted_window[idx])
    series[-1] = latest_value
    # If caller wants to override absolute level, scale.
    if not np.isnan(latest):
        series = series * (latest / latest_value if latest_value > 0 else 1.0)
    return pd.DataFrame({"Close": series})


def _make_iwm(spy_df: pd.DataFrame, *, ratio: float = 1.0) -> pd.DataFrame:
    """IWM closes that produce a target IWM/SPY 20d RS ratio."""
    spy_close = spy_df["Close"].to_numpy()
    iwm = spy_close.copy()
    # Multiply only the last bar so RS = ratio.
    iwm[-1] = iwm[-1] * ratio
    return pd.DataFrame({"Close": iwm})


@pytest.fixture
def patched_data(monkeypatch):
    """Patch `get_ohlc` inside services.swing.regime to return canned frames."""
    state: dict = {"vix": None, "iwm": None}

    def fake_get_ohlc(symbol: str, period: str = "1y") -> pd.DataFrame:
        if symbol == "^VIX":
            return state["vix"]
        if symbol == "IWM":
            return state["iwm"]
        raise AssertionError(f"unexpected get_ohlc({symbol})")

    monkeypatch.setattr(regime_mod, "get_ohlc", fake_get_ohlc)
    return state


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestVixClassification:
    def test_calm_below_25p(self):
        label, score = _classify_vix_regime(vix=14.0, vix_percentile=20.0)
        assert label == "calm"
        assert score == 100.0

    def test_normal_25_to_60p(self):
        label, score = _classify_vix_regime(vix=18.0, vix_percentile=45.0)
        assert label == "normal"
        assert score == 70.0

    def test_elevated_60_to_85p(self):
        label, score = _classify_vix_regime(vix=24.0, vix_percentile=75.0)
        assert label == "elevated"
        assert score == 30.0

    def test_shock_above_85p(self):
        label, score = _classify_vix_regime(vix=38.0, vix_percentile=92.0)
        assert label == "shock"
        assert score == 0.0

    def test_nan_falls_back_to_normal(self):
        label, score = _classify_vix_regime(vix=float("nan"), vix_percentile=float("nan"))
        assert label == "normal"
        assert score == 50.0


class TestIndexTrendClassification:
    def test_full_bull_stack(self):
        label, score = _classify_index_trend(close=520.0, ema21=510.0, ema50=500.0)
        assert label == "bull"
        assert score == 100.0

    def test_full_bear_stack(self):
        label, score = _classify_index_trend(close=480.0, ema21=490.0, ema50=500.0)
        assert label == "bear"
        assert score == 0.0

    def test_mixed_above_50(self):
        label, score = _classify_index_trend(close=505.0, ema21=500.0, ema50=510.0)
        # close > ema50? 505 > 510 → False → score 35
        assert label == "neutral"
        assert score == 35.0

    def test_mixed_below_21_above_50(self):
        # close > ema50 but not above ema21 → 65
        label, score = _classify_index_trend(close=505.0, ema21=510.0, ema50=500.0)
        assert label == "neutral"
        assert score == 65.0


class TestVixPercentile:
    def test_returns_nan_when_too_short(self):
        v, p = _vix_percentile(pd.Series([12.0, 13.0, 14.0]))
        assert np.isnan(v) and np.isnan(p)

    def test_latest_at_top_is_100p(self):
        s = pd.Series(np.linspace(10.0, 30.0, 60))
        v, p = _vix_percentile(s)
        assert v == 30.0
        assert p == 100.0

    def test_latest_at_bottom_is_low_pct(self):
        s = pd.Series(list(np.linspace(30.0, 11.0, 59)) + [10.0])
        v, p = _vix_percentile(s)
        assert v == 10.0
        assert p == pytest.approx(100.0 / 60.0, abs=0.5)


class TestRegimeLabel:
    def test_risk_on_threshold(self):
        assert _label_regime(65.0) == "risk_on"
        assert _label_regime(64.99) == "neutral"

    def test_risk_off_threshold(self):
        assert _label_regime(39.99) == "risk_off"
        assert _label_regime(40.0) == "neutral"


# ---------------------------------------------------------------------------
# compute_regime — integrated path
# ---------------------------------------------------------------------------

class TestComputeRegime:
    def test_risk_on_bull_calm(self, patched_data):
        spy = _make_spy(close=520.0, drift=0.4)  # gentle uptrend → bull stack
        patched_data["vix"] = _make_vix(latest=14.0, percentile_target=15.0)
        patched_data["iwm"] = _make_iwm(spy, ratio=1.04)  # leadership → ~90 score

        state = compute_regime(spy, universe_ohlc=None)

        assert isinstance(state, RegimeState)
        assert state.index_trend == "bull"
        assert state.vol_regime == "calm"
        assert state.regime_label == "risk_on"
        assert state.rr_gate == RR_GATE_BY_REGIME["risk_on"] == 2.5
        assert state.disable_setups == []
        # Multiplier monotone in score
        assert REGIME_MULT_MIN <= state.multiplier <= REGIME_MULT_MAX
        assert state.multiplier > 0.85
        assert state.degraded is False

    def test_risk_off_bear_shock_disables_reversion(self, patched_data):
        spy = _make_spy(close=440.0, drift=-0.5)  # downtrend → bear stack
        patched_data["vix"] = _make_vix(latest=38.0, percentile_target=92.0)
        patched_data["iwm"] = _make_iwm(spy, ratio=0.94)  # small-caps lagging

        state = compute_regime(spy, universe_ohlc=None)

        assert state.index_trend == "bear"
        assert state.vol_regime == "shock"
        assert state.regime_label == "risk_off"
        assert state.rr_gate == RR_GATE_BY_REGIME["risk_off"] == 3.0
        assert "reversion" in state.disable_setups
        assert state.multiplier <= 0.75

    def test_neutral_in_between(self, patched_data):
        # SPY ramp then small dip → close > EMA50 but close < EMA21
        # → index_label "neutral", score 65 (per _classify_index_trend).
        closes = np.concatenate([
            np.linspace(490.0, 525.0, 70),
            np.linspace(525.0, 518.0, 10),
        ])
        spy = pd.DataFrame({
            "Open": closes,
            "High": closes * 1.005,
            "Low": closes * 0.995,
            "Close": closes,
            "Volume": np.full(len(closes), 80_000_000, dtype=float),
        })
        patched_data["vix"] = _make_vix(latest=22.0, percentile_target=70.0)  # elevated
        patched_data["iwm"] = _make_iwm(spy, ratio=1.0)

        state = compute_regime(spy, universe_ohlc=None)

        assert state.index_trend == "neutral"
        assert state.regime_label == "neutral"
        assert state.rr_gate == RR_GATE_BY_REGIME["neutral"] == 2.75
        assert state.disable_setups == []

    def test_missing_spy_degrades_gracefully(self, patched_data):
        # patch VIX + IWM to succeed so degraded only flips for SPY.
        patched_data["vix"] = _make_vix(latest=18.0, percentile_target=45.0)
        # IWM call needs spy_df with len>=21 to compute RS — passing None
        # short-circuits and risk-appetite stays at 50.
        patched_data["iwm"] = pd.DataFrame({"Close": np.linspace(180, 200, 60)})

        state = compute_regime(None, universe_ohlc=None)

        assert state.degraded is True
        assert state.regime_label == "neutral"
        # Defaults to neutral inputs → multiplier must stay in valid band.
        assert REGIME_MULT_MIN <= state.multiplier <= REGIME_MULT_MAX

    def test_breadth_uses_supplied_universe(self, patched_data):
        spy = _make_spy(close=510.0, drift=0.2)
        patched_data["vix"] = _make_vix(latest=18.0, percentile_target=45.0)
        patched_data["iwm"] = _make_iwm(spy, ratio=1.01)

        # 8 of 10 names above their EMA50 → breadth_pct ≈ 80
        universe: dict[str, pd.DataFrame] = {}
        for i in range(10):
            base = 100.0
            up = i < 8
            closes = (
                np.linspace(base * 0.92, base * 1.08, 70)
                if up
                else np.linspace(base * 1.08, base * 0.92, 70)
            )
            universe[f"T{i}"] = pd.DataFrame({"Close": closes})

        state = compute_regime(spy, universe_ohlc=universe)

        assert 70.0 <= state.breadth_pct <= 90.0

    def test_multiplier_bounds_clamped(self, patched_data):
        # Force the absolute best inputs and verify multiplier <= REGIME_MULT_MAX.
        spy = _make_spy(close=600.0, drift=0.5)
        patched_data["vix"] = _make_vix(latest=10.0, percentile_target=1.0)
        patched_data["iwm"] = _make_iwm(spy, ratio=1.10)

        state = compute_regime(spy, universe_ohlc=None)
        assert state.multiplier <= REGIME_MULT_MAX
        assert state.multiplier >= REGIME_MULT_MIN
