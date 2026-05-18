"""Shared fixtures for the screener worker tests.

Pins sys.path to the worker root and evicts sibling-owned module names,
mirroring the pattern used in workers/scorer/tests/conftest.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WORKER_ROOT = str(Path(__file__).resolve().parent.parent)
_SIBLING_MODULE_NAMES = ("main", "config", "cosmos_client", "runner", "market_hours")


@pytest.fixture(autouse=True)
def _pin_screener_root() -> None:
    if _WORKER_ROOT in sys.path:
        sys.path.remove(_WORKER_ROOT)
    sys.path.insert(0, _WORKER_ROOT)
    for name in _SIBLING_MODULE_NAMES:
        module = sys.modules.get(name)
        if module is None:
            continue
        module_file = getattr(module, "__file__", "") or ""
        if _WORKER_ROOT not in module_file:
            del sys.modules[name]
