# ADR-0035 — Replace HDBSCAN with Cosine-Graph Connected Components

- **Status**: Accepted
- **Date**: 2026-05-21
- **Supersedes**: [ADR-0017](0017-narrative-phase5-detector.md) (HDBSCAN design), [ADR-0026](0026-hdbscan-intra-cluster-similarity-floor.md) (intra-cluster floor)
- **Related code**: `workers/narrative-detector/detector.py`

## Context

ADR-0017 specified HDBSCAN (min_cluster_size=3) as the clustering algorithm for
narrative thread detection.  ADR-0026 added an intra-cluster similarity floor (0.35)
to demote low-coherence HDBSCAN clusters that formed purely because two posts were
nearest neighbours in a sparse embedding space.

Both ADRs acknowledged the sparse-signal problem: at typical ingestion volumes of
2–18 signals per ticker per 72-hour window, HDBSCAN needs `min_cluster_size=3` to
avoid pairing random noise, but this also means most tickers have all signals labelled
as noise (−1).  The result: lifecycle stage assignment was dominated by the EMA
hysteresis starting value (0), producing large numbers of Stage 0 ("insufficient data")
tickers even when 8–12 semantically coherent signals existed.

An adversarial audit (May 2026) quantified this:

> "At 2–18 signals per 72h, HDBSCAN with min_cluster_size=3 produces singleton or
> empty clusters for most tickers, making lifecycle stage assignment functionally
> arbitrary."

Additionally, HDBSCAN is non-deterministic across sklearn minor versions (due to
tie-breaking differences in the condensed tree), introducing non-reproducibility
across Container Apps Job pod restarts.

## Decision

Replace HDBSCAN + centroid merging + intra-cluster floor with a **pairwise cosine
similarity graph** + **connected components** approach:

```
1. Normalise all embeddings to unit vectors.
2. Compute all-pairs cosine similarity matrix (O(n²)).
3. Build adjacency graph: edge(i, j) = 1 iff cosine(i, j) >= CLUSTER_SIMILARITY_FLOOR (0.45).
4. Find connected components via union-find.
5. Singleton components (no edges) → label −1 (noise).
6. Multi-member components → labelled 0, 1, 2 … by descending size.
```

**CLUSTER_SIMILARITY_FLOOR = 0.45** — chosen to be above the intra-cluster noise
floor observed in ADR-0026 (0.35) and calibrated to produce coherent narrative groups
on the production signal corpus.

## Rationale

| Property | HDBSCAN | Cosine-graph |
|---|---|---|
| Minimum cluster size | 3 (configurable, but must be ≥2) | 2 (any pair above floor) |
| Handles 2 signals | All noise | Forms 1 cluster if sim ≥ floor |
| Deterministic | No (tie-breaking) | Yes |
| Hyperparameters | min_cluster_size, min_samples, merge_threshold, intra_similarity_floor | similarity_floor only |
| Interpretability | Non-obvious DBSCAN density semantics | "these two posts are semantically similar" |
| O(n²) | Yes | Yes |
| sklearn dependency | sklearn.cluster.HDBSCAN | sklearn.metrics.pairwise only |

The cosine-graph approach is strictly more interpretable: a cluster exists iff every
member pair has cosine similarity ≥ 0.45.  This is a direct operationalisation of
"shared narrative thread" without the HDBSCAN density abstraction.

## Consequences

**Positive:**
- Stage assignment is now possible for tickers with as few as 2 semantically similar
  signals — the entire early-detection use case.
- Eliminates the centroid-merging and intra-cluster floor logic (both complexity and
  failure modes).
- Fully deterministic — identical embeddings always produce identical labels across
  pod restarts.
- Removes HDBSCAN as a dependency; `sklearn.metrics.pairwise` is already used.

**Negative / risks:**
- Transitivity: if A is similar to B and B is similar to C but A is not similar to C,
  all three are in the same cluster.  This "chaining" effect can occasionally produce
  larger, less coherent clusters than HDBSCAN when signal volumes are high.  Monitor
  dominant_fraction values: if dominant_fraction < 0.4, the cluster may be too broad.
- At high signal volumes (>100/72h), O(n²) computation remains fast (<1 ms for
  n=100, ~10 ms for n=500) but should be monitored if ingestion scales.

## Migration

Existing `lifecycle_state.smoothed_inputs` stored in Cosmos `ticker_timeline`
documents remain valid — the EMA state is over the aggregated breadth scores, not the
cluster labels directly.  The hysteresis (ADR-0030) will absorb the transition as the
detector re-runs over the next 6–8 hours (2 × `confirm_runs` cycles).

No Cosmos schema migration is required.
