# ADR-0026 — HDBSCAN Intra-Cluster Similarity Floor

**Status:** Accepted  
**Date:** 2026-05-18  
**Supersedes (in part):** [ADR-0017](0017-narrative-phase5-detector.md) — clustering quality control  
**Related:** [ADR-0017](0017-narrative-phase5-detector.md), [ADR-0018](0018-classifier-embedding-soft-fail.md)

---

## Context

ADR-0017 set `min_cluster_size=3` as the HDBSCAN parameter for the narrative detector.
At current ingestion volumes (~2–18 signals per ticker per 72-hour window), most tickers
never accumulate 3 posts in the same semantic region, so HDBSCAN labels all points as
noise and returns `n_clusters=0` → `lifecycle_stage=0`. This was diagnosed and a
temporary fix of `MIN_CLUSTER_SIZE=2` was deployed as an Azure Container Apps Job env
var.

Lowering `min_cluster_size` to 2 solves the false-negative problem (real signal pairs
visible to the screener) but introduces a false-positive risk: any two posts that happen
to be nearest neighbours in embedding space can form a cluster, regardless of whether
they discuss the same thesis. In a 1536-dimensional embedding of ticker-domain text, even
semantically unrelated posts share vocabulary (the ticker symbol, earnings, options
terminology) and can score a non-trivial cosine similarity — enough for HDBSCAN with
`min_samples=1` to link them.

The false-positive rate is highest at `n=2` (a "cluster" of exactly 2 posts always
forms, no matter how semantically distant they are) and decreases rapidly as `n` grows
because genuine narratives produce multiple confirming posts while random pairs do not.

### Why `min_cluster_size` alone is insufficient

`min_cluster_size` controls *count*; it says nothing about *semantic coherence*. A
cluster of 4 posts about completely different topics (macro bearish, earnings bull,
technical breakout, dividend cut) can be formed if those 4 posts happen to be the
densest neighbourhood in a sparse space. The downstream stage rules (Gini, `tier1_pct`,
`bull_share`) partially mitigate this, but only after the cluster has already been
promoted to a stage — a fundamentally weaker gate.

---

## Decision

Add a **post-HDBSCAN, post-merge intra-cluster similarity floor** to `cluster()` in
`workers/narrative-detector/detector.py`.

After HDBSCAN runs and after the centroid-merge step, each surviving cluster is evaluated
against a mean pairwise cosine similarity threshold. Clusters that fall below the floor
are demoted to noise (their labels set to `−1`) and excluded from lifecycle assignment,
exactly as HDBSCAN's own noise points are.

### Algorithm

For each cluster label `k` with member indices `I_k`:

$$\bar{s}_k = \frac{1}{|I_k|(|I_k|-1)} \sum_{i \in I_k} \sum_{\substack{j \in I_k \\ j \neq i}} \cos(e_i, e_j)$$

If $\bar{s}_k < \tau_{\text{intra}}$, demote cluster $k$ to noise.

This uses the already-computed `cos_sim` matrix (the same one passed to HDBSCAN as a
distance matrix), so there is no additional embedding API call and no meaningful compute
overhead ($O(|I_k|^2)$ element accesses per cluster, typically $|I_k| \leq 10$).

### Parameter

| Parameter | Env var | Code default | Rationale |
|---|---|---|---|
| `min_intra_cluster_similarity` | `MIN_INTRA_CLUSTER_SIMILARITY` | `0.35` | Cosine similarity in ada-002 1536-dim space: unrelated ticker posts ≈ 0.10–0.25; same-topic posts ≈ 0.40–0.75; identical rationale ≈ 1.00. Floor at 0.35 passes genuine narrative pairs while rejecting domain-vocabulary coincidences. |

The default of **0.35** was chosen based on the distribution of pairwise cosine
similarities observed in the `signals` Cosmos container:

- Random same-ticker posts with different topics: 0.10–0.28
- Posts referencing the same news event (e.g. earnings beat) from different angles: 0.38–0.62
- Near-duplicate GPT rationales about the same story: 0.80–1.00

A floor of 0.35 sits just above the noise band and just below the genuine-narrative band.
It is intentionally conservative: a genuine narrative cluster that falls between 0.35 and
0.40 will be accepted, and only clusters solidly in the random-pair range (< 0.35) are
rejected.

### Interaction with `min_cluster_size=2` (current env override)

The floor is the semantic complement to `min_cluster_size`:

| Gate | What it controls |
|---|---|
| `min_cluster_size=2` | Minimum *count* — any 2 posts can pass the count gate |
| `min_intra_cluster_similarity=0.35` | Minimum *coherence* — the pair must discuss a related thesis |

Together: a two-post cluster is accepted only if its two posts are semantically aligned.
A two-post cluster of an unrelated bull/bear pair is demoted to noise.

### When to revisit `min_cluster_size`

As ingestion volume grows (more subreddits, higher posting frequency), the median signals
per ticker per 72h will increase. When the **median crosses ~6**, the statistical benefit
of `min_cluster_size=3` (requiring three corroborating posts) outweighs its recall cost.
At that point, reset `MIN_CLUSTER_SIZE=3` on `job-narrative-detector`. The similarity
floor remains valid regardless of which `min_cluster_size` is active.

---

## Alternatives considered

### A — Raise `merge_threshold` to reduce spurious merges

Does not help: merge_threshold governs whether two *separate* clusters become one; it
does not eliminate a single low-coherence cluster that HDBSCAN already produced
internally.

### B — Centroid-to-centroid similarity (already implemented via merge_threshold)

The existing merge step compares centroid-to-centroid similarity. For a 2-point cluster,
the centroid is the mean of the two embeddings — which scores *higher* than the raw
pairwise similarity between the two posts because averaging dampens the directional
difference. Mean pairwise similarity is a stricter gate.

### C — Keep `min_cluster_size=3` permanently

Fixes false positives at the cost of high false negatives (~82% of tickers stage=0 at
current volumes). Not viable until ingestion scale supports it.

### D — Minimum cosine similarity on the distance matrix before HDBSCAN

Pre-filtering the distance matrix (setting D[i,j] = ∞ for pairs with cosine < threshold)
forces HDBSCAN to never link those pairs. Achieves a similar outcome but is harder to
reason about, changes the HDBSCAN density landscape for all points, and makes the
parameter interaction with `min_cluster_size` more complex. Post-hoc demotion is
equivalent in outcome and simpler to audit.

---

## Consequences

**Positive:**
- Eliminates stage promotions driven by random post pairs (false positives with
  `min_cluster_size=2`).
- Semantic quality gate is independent of count — still effective as volume grows.
- No new dependencies, no additional API calls, negligible compute cost.
- Threshold is env-configurable for future calibration without a redeploy.

**Negative:**
- Slightly increases the false-negative rate at very low volumes (2-post clusters that
  are genuine but score 0.30–0.35 similarity will be demoted). Acceptable: a low-coherence
  pair is weak evidence of a narrative regardless.
- The 0.35 threshold is calibrated on current ada-002 embeddings. If the embedding model
  changes, recalibrate before deploying.

---

## Follow-ups

- **Duplicate rationale dedup in classifier** — Two different posts that trigger identical
  GPT rationale produce cosine similarity = 1.00. The similarity floor accepts these
  (correctly — they are semantically aligned) but they represent one real data point, not
  two independent signals. A dedup pass in `job-classifier` before the embedding call would
  prevent inflating `conviction_bull_share` and contributor counts.
- **Recalibrate floor when embedding model changes** — If ada-002 is replaced, the absolute
  cosine similarity scale shifts. Recalibrate `MIN_INTRA_CLUSTER_SIMILARITY` against new
  embedding distributions before deploying.
- **Revert `MIN_CLUSTER_SIZE` to 3** — Track median signal count per ticker. When median ≥ 6,
  flip the Azure env var back to `MIN_CLUSTER_SIZE=3`.
