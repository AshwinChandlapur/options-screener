# Options Screener — Project Guidelines

## What this repo is

A FastAPI + React options screener with three strategies (CSP, CC, DITM), a DCF valuation tab (currently hidden), and a Supply Chain visualization. Backend is Python 3.12 / FastAPI / yfinance / Azure OpenAI; frontend is React 19 + Vite + TypeScript. Deployed to Azure Static Web Apps (frontend) and Azure Web App (backend).

## Architecture (current state)

Strict layering, in this order — never reverse:

```
HTTP request
  → backend/routers/*.py        (request/response shapes, validation, rate limiting)
    → backend/services/*.py     (domain logic, scoring, orchestration)
      → backend/services/data_service.py | options_service.py  (external data adapters)
        → yfinance / SEC EDGAR / Azure OpenAI
```

Rules:
- **Routers must not contain business logic.** They validate, delegate, convert. Anything else belongs in `services/`.
- **Services must not import FastAPI types** (`Request`, `HTTPException`, etc.). Raise typed domain exceptions; routers map them to HTTP.
- **No service-to-router imports.** Ever.
- **Cross-service imports are allowed** but should flow downward: `csp_service` may use `data_service`, never vice versa.

Frontend mirrors this:
```
App.tsx → components/* → hooks/* → fetch()
```
Components don't `fetch()` directly — always go through a hook.

## Code Style

- Python: type hints on all public function signatures. Prefer `dataclass` over dict for structured returns. Constants `UPPER_SNAKE`, internal helpers `_leading_underscore`.
- TypeScript: function components + hooks only. No class components. No `any` unless explicitly justified in a comment. Strict optional checks.
- Both: keep functions small. If you're scrolling to read one function, it's too big.

## Conventions that differ from "default" practice

- **Scoring constants are sacred.** `ENV_WEIGHTS`, `STRIKE_WEIGHTS`, calibration curves in `backend/services/technical_service.py` (and successors) define the screener's identity. Don't tweak without an ADR + matching update in `SCORING_REFERENCE.md` and the frontend `SCORE_LEGEND` arrays.
- **Universe is curated, not algorithmic.** `backend/services/universe.py` is the single source of truth. Don't introduce parallel ticker lists.
- **DCF tab is hidden but live.** Don't delete its code; we're parking it pending verdict calibration.
- **Methodology docs are first-class.** Any change to scoring math, DCF assumptions, or supply-chain extraction logic requires updating `docs/*.md` in the same PR.
- **Em-dashes (—) are intentional in user-facing copy.** Don't ASCII-fy them.

## Build and Test

```pwsh
# Backend (from repo root)
cd backend
.\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload

# Frontend
cd frontend
npm run dev      # Vite dev server (port 5173)
npm run build    # Production build (must pass before push)
```

Tests / lint / pre-commit: not yet set up — see Track A Phase 3+ in `/memories/session/plan-master.md`. New code should still be written test-ready (typed, side-effect-injectable).

## Working with the SWE Agent Team

Specialist agents live in `.github/agents/`. **Feature work stays in the default Copilot Chat agent.** Specialists are checkpoint reviewers/finishers, not feature builders.

Workflow for a non-trivial change:
1. (default agent) Plan + implement.
2. `@architect` for design check on substantial changes.
3. `@test-engineer` to scaffold tests.
4. `@reviewer` for diff-level review before commit.
5. `@docscribe` to sync methodology docs and write ADRs when warranted.
6. `@devops` for CI/deploy/env config changes.
7. `@tech-lead` for orchestrated multi-area passes ("have the team review").

**Advisory vs write-enabled:**
- `@architect`, `@reviewer`, `@tech-lead` — read-only.
- `@test-engineer` — writes only under `backend/tests/**` or `frontend/src/**/*.test.{ts,tsx}`.
- `@docscribe` — writes only `**/*.md`.
- `@devops` — writes only `.github/workflows/**`, `.azure/**`, `Dockerfile`, env templates, `.pre-commit-config.yaml`.

Path restrictions are documented conventions, not VS Code-enforced. Git remains the human gate.

## Hard rules (zero exceptions)

- **No secrets in source.** Use env vars. `backend/.env.example` is the contract.
- **No new top-level dependencies without justification** in the PR description.
- **No silent network calls in tests.** Always mock yfinance, SEC, Azure OpenAI.
- **Methodology and code stay in lockstep.** If math changes, the doc changes in the same PR.
- **Commit messages follow conventional style**: `feat:`, `fix:`, `refactor:`, `docs:`, `chore:`, `test:`. Multi-paragraph bodies for non-trivial changes.

## Pointers

- Detailed scoring: [SCORING_REFERENCE.md](../SCORING_REFERENCE.md)
- DCF math: [docs/DCF_METHODOLOGY.md](../docs/DCF_METHODOLOGY.md)
- Supply chain: [docs/SUPPLY_CHAIN.md](../docs/SUPPLY_CHAIN.md)
- Active plans: see `/memories/session/plan-master.md` (workflow), `plan-agents.md`, `plan-screener-refactor.md`.
