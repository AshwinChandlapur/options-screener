/**
 * Signal Performance types — mirrors backend/services/narrative/signals_service.py
 * and the GET /api/narrative/signals response shape.
 *
 * See docs/adr/0030 (forward signal log) for the data contract.
 */

export interface SignalEvent {
  id: string
  ticker: string
  event_date: string            // ISO date "YYYY-MM-DD"
  event_ts: string              // ISO 8601 UTC
  prev_stage: number            // 0..6
  new_stage: number             // 0..6
  /** Encoded "{prev}to{new}", e.g. "2to3". */
  transition: string
  confidence: number            // 0..1
  breadth_score: number | null
  breadth_delta: number | null
  px_at_signal: number | null
  px_t5: number | null
  px_t10: number | null
  px_t20: number | null
  spy_at_signal: number | null
  spy_t5: number | null
  spy_t10: number | null
  spy_t20: number | null
  backfilled_at: string | null
  /** Ticker return − SPY return at T+N; null when prices not yet hydrated. */
  excess_t5: number | null
  excess_t10: number | null
  excess_t20: number | null
}

export interface HorizonStats {
  horizon_days: number          // 5, 10, or 20
  n_complete: number            // events with all four prices populated
  hit_rate: number | null       // 0..1, null when n_complete == 0
  median_excess_return: number | null
}

export interface SignalsResponse {
  n_total: number
  horizons: HorizonStats[]
  events: SignalEvent[]
}

export interface SignalsFilters {
  since: string | null          // ISO date or null
  minConfidence: number | null  // 0..1 or null
  transition: string | null     // "2to3" etc, or null for any
  ticker: string | null
}
