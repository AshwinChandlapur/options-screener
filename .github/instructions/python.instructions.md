---
description: "Python coding standards for the Options Screener backend. Use when editing backend/**/*.py — covers type hints, FastAPI layering, pytest conventions, and external-service wrapping."
applyTo: "backend/**/*.py"
---

# Python Standards (Backend)

## Layering

- **Routers** (`backend/routers/`): thin. Validate input, rate-limit, delegate to a service, map domain errors → `HTTPException`. No business logic.
- **Services** (`backend/services/`): own all domain logic. **Never import** `fastapi.HTTPException`, `Request`, `Response`. Raise typed domain errors (e.g., `CspError(symbol=..., reason=...)`) and let routers translate.
- **Adapters** (`data_service.py`, `options_service.py`): wrap external sources (yfinance, SEC, Azure OpenAI). Services depend on adapters, not the other way around.

## Types

- Public functions and dataclass fields are fully type-annotated. `def get_ohlc(symbol: str, period: str = "2y") -> pd.DataFrame:` not bare.
- Prefer `@dataclass(frozen=True)` for return types over `dict[str, Any]`. Frontend types depend on these shapes — drift is silent.
- Use `Literal[...]` for closed string sets (e.g., `direction: Literal["short_put", "short_call", "long_call"]`).
- `from __future__ import annotations` at the top of every module.

## Errors

- One typed exception class per service domain (`CspError`, `CcError`, `DitmError`, `DcfError`, `SupplyChainError`). Carry context (`symbol`, `reason`, optional `cause`).
- `except Exception:` is allowed only at the orchestration boundary (one symbol failing must not crash the scan). Always log with `logger.exception` before swallowing.
- Do not catch and re-raise without adding context.

## External services

- **yfinance**: every call lives in `data_service.py` or `options_service.py`. No `import yfinance` outside those files. Wrap in try/except for network errors; return `None`/empty DataFrame on failure with a logged warning.
- **Azure OpenAI**: every call lives in `dcf_service.py` or `supply_chain_service.py`. Use `response_format={"type": "json_object"}` for structured extraction. Never log the raw API key.
- **SEC EDGAR**: use the existing `SEC_HEADERS` and `httpx.Client(timeout=30)` pattern in `supply_chain_service.py`. Always set `User-Agent`.

## Configuration

- Read env vars at module scope only for read-only constants. Validate required secrets at import time when added — empty defaults are not acceptable for `AZURE_OPENAI_KEY`-class values.
- No hardcoded secrets, ever.

## Logging

- One module-level logger per file: `logger = logging.getLogger(__name__)`.
- `logger.info` for orchestration milestones, `logger.warning` for recoverable issues, `logger.exception` for swallowed errors.
- Never `print()` in service or router code.

## Testing

- Tests live under `backend/tests/{unit,integration}/`. Mirror the source tree.
- Mock all external I/O. yfinance and Azure OpenAI calls in tests are a hard fail.
- Use `@pytest.fixture` for shared setup; avoid module-level state.

## Style nits

- Constants `UPPER_SNAKE`. Module-private helpers `_leading_underscore`.
- Line length 100 (matches future `black` config).
- f-strings over `.format()` and `%`.
- `pathlib.Path` over `os.path.join`.
