---
description: "Test-writing standards. Use when writing or reviewing test files (test_*.py, *.test.ts, *.test.tsx) — covers AAA pattern, mocking external services, fixtures, and naming."
applyTo: "**/{test_*.py,*.test.ts,*.test.tsx,conftest.py}"
---

# Test Writing Standards

## Structure (AAA)

Every test follows **Arrange / Act / Assert**, separated by blank lines:

```python
def test_get_universe_unknown_falls_back_to_all():
    # Arrange
    name = "does-not-exist"

    # Act
    key, syms = get_universe(name)

    # Assert
    assert key == "all"
    assert len(syms) > 0
```

No "Arrange-Act-Assert-Act-Assert" — one act per test. Split if you have multiple.

## Naming

- **Python**: `test_<unit>_<scenario>_<expected>` — e.g., `test_compute_env_score_strong_uptrend_returns_high_score`.
- **TS**: `describe('<unit>', () => { it('<scenario, expected>') })` — e.g., `it('returns 113 for the "all" universe')`.
- Don't include the word "should" or "test" in the body of the name; the framework adds them.

## No network in unit tests

- **Hard rule.** If a unit test makes a real HTTP call (yfinance, SEC, Azure OpenAI, anything), it's broken.
- Backend: mock with `unittest.mock.patch` at the import site, or pass a fake adapter via dependency injection.
- Frontend: mock `fetch` with `vi.fn().mockResolvedValue(...)` in `beforeEach`.

## Fixtures

- **Python**: shared fixtures in `backend/tests/conftest.py`. Scoped narrowly (function > module > session). Prefer factory fixtures over big fixed dicts so each test composes its own state.
- **TS**: shared mock factories under `frontend/src/__tests__/helpers/`.

## Determinism

- No `datetime.now()` / `Date.now()` directly in code paths under test — pass a clock function. If unavoidable, freeze with `freezegun` (Python) or `vi.useFakeTimers()` (TS).
- No `random` without a seed.
- No filesystem writes outside `tmp_path` (Python) or `vi`'s temp utilities.

## What to test

- **Pure functions and scoring math**: cover edge cases (zero, negative, NaN inputs) and at least one happy path.
- **Service orchestration**: cover the success path + at least one failure-mode-recovery path (e.g., yfinance timeout returns empty results, not crash).
- **Dataclass shapes**: don't test the dataclass itself; test that the producing function fills the right fields.
- **Routers**: integration test with `TestClient`, mocking the service layer.

## What not to test

- Third-party library behavior (yfinance internals, FastAPI routing).
- Trivial getters/setters.
- Implementation details that could change without a behavior change.

## Characterization tests (refactor safety nets)

When refactoring, capture current outputs as JSON fixtures under `backend/tests/fixtures/` and assert equality. These are **throwaway**; they protect a refactor and can be deleted once stable. Mark them clearly:

```python
# Characterization test — captures pre-refactor behavior of process_symbol.
# Delete after Track B Phase 4 if all production tests cover the path.
```

## Coverage

- Aim for meaningful coverage, not a number. A test that just calls a function without asserting useful properties is worse than no test.
- Scoring functions and the screener runner should approach 100%; UI components 50%+ is fine.
