---
description: "React + TypeScript coding standards for the Options Screener frontend. Use when editing frontend/src/**/*.{ts,tsx} — covers component structure, hooks, type safety, API access, and styling."
applyTo: "frontend/src/**/*.{ts,tsx}"
---

# React + TypeScript Standards (Frontend)

## Component structure

- **Function components only.** No class components.
- One component per file. Filename matches the component (PascalCase: `CspInput.tsx`).
- Components live in `frontend/src/components/`. Hooks in `frontend/src/hooks/`. Types in `frontend/src/types/`. Constants in `frontend/src/constants/`.
- Components do **not** call `fetch()`. They consume data via a hook (`useCsp`, `useCc`, `useDitm`, `useDcf`, `useSupplyChain`).

## Hooks

- One hook per data domain. Name `use<Domain>` (e.g., `useCsp.ts`).
- Hook owns: loading state, error state, results, fetch function. Returns a stable object.
- Side-effecting hooks must respect React 19 strict-mode double-invoke: cleanup in `useEffect`.

## Types

- `tsconfig.json` is `strict: true` (when added). Treat it as already on.
- No `any` without an inline `// eslint-disable-next-line` plus a justification comment. Prefer `unknown` and narrow.
- API response types live in `frontend/src/types/`. They mirror backend dataclasses field-for-field. When a backend dataclass changes, update the type in the same PR.
- Use `Literal` unions for closed string sets (e.g., `type UniverseKey = "all" | "ai_full" | "ai_chips" | ...`).

## State management

- Local state: `useState` / `useReducer`.
- Cross-component state: lift to nearest common parent. No Context API yet — when one is needed, document in an ADR first.
- No Redux / Zustand / MobX without an ADR.

## API access

- Single base URL constant: `API_BASE` from `import.meta.env.VITE_API_BASE`. No hardcoded URLs.
- Errors from the backend come back as `{ detail: string }` JSON. Surface the message; do not swallow.
- All `fetch` calls live in `frontend/src/hooks/` or a future `frontend/src/api/` module.

## Styling

- Global stylesheet: `frontend/src/index.css`. Class-based, kebab-case (`.filter-select`, `.score-tile-2`).
- No inline styles unless one-off (e.g., dynamic `style={{ width: pct + '%' }}`). For static styles, add a class.
- Dark theme palette: bg `#0f1117` / `#1a1d27`, border `#2d3148`, text `#e2e8f0` / muted `#94a3b8`. Match this when adding components.
- Use CSS `:hover` / `:focus` states, not JS-driven hover.

## Score legend / scoring constants

- Frontend mirrors backend scoring weights via `SCORE_LEGEND` arrays. **Treat these as derived data**: when backend `ENV_WEIGHTS` / `STRIKE_WEIGHTS` change, update both the legend arrays and `SCORING_REFERENCE.md` in the same PR.
- Never hand-edit numbers in legend arrays without a corresponding backend change.

## Build hygiene

- `npm run build` must pass before pushing.
- No unused imports. No unused props. No commented-out code blocks (use git history).
- Prefer named exports over default exports.

## Testing (when added)

- Tests colocated as `*.test.tsx` next to the component, or under `frontend/src/__tests__/`.
- Use `vitest` + `@testing-library/react`. Mock `fetch` with `vi.fn()`; never hit a real backend.
