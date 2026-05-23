"""Smoke-test the staged ETV pipeline."""
from __future__ import annotations

import json
import os
import time

from dotenv import load_dotenv

load_dotenv()
os.environ["ETV_PIPELINE_STAGED"] = "1"

from services.etv import get_etv  # noqa: E402  imports after env setup

TICKER = os.environ.get("SMOKE_TICKER", "MSFT")

t0 = time.time()
r = get_etv(TICKER, refresh=True)
dt = time.time() - t0

print(f"== wall: {dt:.1f}s   pipeline_enabled={r['pipeline_enabled']}")
print("-- pipeline_log --")
print(json.dumps(r["pipeline_log"], indent=2))

ms = r["report"].get("model_selection", {})
print("-- model_selection --")
print(json.dumps(
    {k: ms.get(k) for k in (
        "primary_archetype", "primary_model",
        "primary_model_rationale", "selection_confidence",
    )},
    indent=2,
))

ev = r["report"].get("economic_value", {})
print("-- economic_value summary --")
print(json.dumps({
    "central_estimate": ev.get("central_estimate"),
    "low_range": ev.get("low_range"),
    "high_range": ev.get("high_range"),
    "bear_price": ev.get("bear", {}).get("price"),
    "base_price": ev.get("base", {}).get("price"),
    "bull_price": ev.get("bull", {}).get("price"),
    "bear_derivation": ev.get("bear", {}).get("derivation"),
    "base_derivation": ev.get("base", {}).get("derivation"),
    "bull_derivation": ev.get("bull", {}).get("derivation"),
}, indent=2))

print("-- missing_inputs --")
print(json.dumps(r["report"].get("missing_inputs", []), indent=2))

print("-- validation --")
print(json.dumps(r["report"].get("validation", {}), indent=2))
