---
description: "Documentation standards. Use when editing or creating Markdown docs (README, methodology docs, ADRs, runbooks) — covers structure, ADR template, methodology-code lockstep, and link discipline."
applyTo: "**/*.md"
---

# Documentation Standards

## Doc taxonomy

| Doc | Audience | Lives at |
|-----|----------|----------|
| `README.md` | First-time visitor | repo root |
| `SCORING_REFERENCE.md` | Anyone using the screener | repo root |
| `docs/<TOPIC>.md` | Methodology deep-dives (DCF, supply chain, scoring math) | `docs/` |
| `docs/adr/NNNN-<slug>.md` | Architectural Decision Records | `docs/adr/` |
| `docs/ARCHITECTURE.md` | Layering + invariants reference | `docs/` |
| `docs/CONTRIBUTING.md` | How to develop, test, commit | `docs/` |
| `docs/DEPLOYMENT.md` | Azure deploy, env vars, rollback | `docs/` |

## Methodology docs

- Treat methodology docs as **first-class code artifacts**, not afterthoughts.
- Every doc opens with a one-paragraph "what + why", then a section index.
- When the math/algorithm changes, the doc changes **in the same PR**. CI should reject otherwise (when CI lands).
- Use KaTeX for formulas: `$score = 0.4 \cdot env + 0.6 \cdot strike$`. Inline `$...$`, block `$$...$$`.
- Cite weight tables verbatim; do not paraphrase. If it differs from the code, the code wins and the doc must update.

## ADRs

ADR template (Michael Nygard style):

```markdown
# ADR-NNNN: <title>

- **Status**: Proposed | Accepted | Deprecated | Superseded by ADR-MMMM
- **Date**: YYYY-MM-DD

## Context
What is the issue motivating this decision? What forces are at play (tech, business, team)?

## Options Considered
1. **Option A** — pros/cons.
2. **Option B** — pros/cons.
3. **Option C** — pros/cons.

## Decision
What we're doing and why.

## Consequences
- Positive: ...
- Negative: ...
- Neutral: ...

## Follow-ups
- [ ] Items the team needs to do as a result.
```

Number ADRs sequentially (`0001`, `0002`, ...). Never renumber. When superseding, mark old as `Superseded by ADR-NNNN` and link.

## Style

- Use Title Case for `# H1`; sentence case for lower headings.
- Em-dashes (—) are intentional. Don't ASCII-fy to `--`.
- Prefer bullets over prose for lists, prose over bullets for reasoning.
- Code blocks have language tags (` ```python `, ` ```pwsh `, ` ```typescript `).
- Tables for structured comparison; not for layout.

## Links

- Internal links use repo-relative paths: `[Scoring](SCORING_REFERENCE.md)` from root, `[DCF math](DCF_METHODOLOGY.md)` inside `docs/`.
- External links: prefer permanent URLs (avoid mutable wiki redirects).
- Validate links when editing — broken links are a maintenance smell.

## Length

- README: under 250 lines. Link out for depth.
- Methodology docs: as long as needed; section index mandatory above ~200 lines.
- ADRs: 100–300 lines. If longer, split into multiple ADRs.
