/**
 * Narrative intelligence types — mirrors backend/services/narrative/types.py
 * and backend/routers/narrative.py response shapes.
 *
 * See docs/NARRATIVE_METHODOLOGY.md §5 for ACS component definitions.
 */

export interface AcsComponents {
  a_attention_persistence: number  // 0..30 (post ADR-0034)
  b_contributor_quality: number    // 0..25
  c_narrative_strength: number     // 0..25
  d_thesis_quality: number         // 0..20
  e_market_confirmation: number    // always 0 — retired (ADR-0034)
}

export interface AcsScore {
  ticker: string
  scored_at: string  // ISO 8601
  acs: number        // 0..100
  acs_ci_lower: number
  acs_ci_upper: number
  components: AcsComponents
  dominant_signal: string
  decay_acs: number
  flags: string[]
  /** 0 = unknown (detector hasn't run), 1..6 per methodology §4. */
  lifecycle_stage: number
  stage_confidence: number  // 0..1
  // ADR-0023 — continuity fields surfaced on Top + Emerging tables.
  /** Consecutive days ending today where lifecycle_stage ∈ {1,2,3}. */
  stage_streak_days?: number
  /** ISO date of the first day in the current streak (null when streak = 0). */
  first_emerged_at?: string | null
  /** OLS slope of ACS over the last 14 daily snapshots; null if <5 samples. */
  acs_slope_14d?: number | null
  /** Market cap in USD at score time; null when yfinance lookup failed. */
  market_cap?: number | null
}

export interface DailyBucket {
  day: string  // ISO date
  count: number
  unique_authors: number
}

export interface TickerDetail {
  ticker: string
  bucket_date: string
  score: AcsScore
  daily_buckets: DailyBucket[]
  tier1_pct: number  // 0..1
  tier2_pct: number
  tier3_pct: number
  mentions_14d: number
  unique_authors_14d: number
  gini_14d: number
  contributor_count_growth_7d: number
  // Conviction axes (ADR-0020 / ADR-0021). All null until the axis-aware
  // classifier has labelled at least one signal in the 14d window.
  conviction_bull_share: number | null
  conviction_researched_share: number | null
  conviction_entering_share: number | null
  conviction_exiting_share: number | null
  conviction_driver_top: string | null
  conviction_bull_researched_share: number | null
  conviction_bear_researched_share: number | null
  conviction_classified_14d: number | null
}

export type LifecycleStage = 1 | 2 | 3 | 4 | 5 | 6

export interface NarrativeCluster {
  narrative_id: string  // UUID
  label: string
  associated_tickers: string[]
  lifecycle_stage: LifecycleStage
  stage_confidence: number  // 0..1
  velocity_14d: number
  cross_sub_count: number
  top_terms: string[]
  first_seen_utc: string
  last_updated_utc: string
}

export interface NarrativeAlert {
  ticker: string
  alert_type: string
  triggered_at: string
  payload: Record<string, unknown>
}

export interface NarrativeError {
  detail: string
  /** True when the platform isn't yet provisioned (Phase 0 → 503). */
  unavailable: boolean
}

// ---------------------------------------------------------------------------
// IC Monitor — GET /api/narrative/ic-monitor
// ---------------------------------------------------------------------------

export interface WeeklyIcPoint {
  week_label: string           // "2026-W22"
  n_pairs: number              // complete (ACS, return) pairs in this cohort
  ic: number | null            // Spearman IC; null if cohort < 10
  p_value: number | null
  mean_acs: number
  mean_return_pct: number
}

export interface FactorIcEntry {
  factor: string               // e.g. "comp_a", "gini_14d"
  n: number                    // complete pairs with this factor present
  ic: number | null            // Spearman rho; null if n < 10
  p_value: number | null
}

export interface AsymmetryBucket {
  label: string                // e.g. "ACS top quartile", "Emerging (stage 2–3)"
  n: number
  mean_ret: number | null
  median_ret: number | null
  std_ret: number | null
  skewness: number | null      // > 0 = right-skewed (fat right tail)
  win_rate: number | null      // fraction of returns > 0
  upside_10: number | null     // fraction of returns > +10%
  downside_10: number | null   // fraction of returns < −10%
  tail_ratio: number | null    // upside_10 / downside_10 ; >1 = right asymmetry
}

export interface IcMonitor {
  forward_days: number
  cumulative_ic: number | null
  cumulative_n: number
  cumulative_p_value: number | null
  weekly: WeeklyIcPoint[]
  factor_ics: FactorIcEntry[]  // per-factor IC across all complete pairs
  asymmetry: AsymmetryBucket[] // return distribution by ACS/stage segment
  total_snapshots: number
  total_complete: number
  pct_complete: number         // 0..1
  window_start: string | null
  window_end: string | null
  last_computed_at: string
}
