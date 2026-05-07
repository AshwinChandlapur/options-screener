export interface EmRankStrikeInfo {
  strike: number
  bid: number
  ask: number
  mid: number
  spread_pct: number | null
  delta: number
  oi_vol: number
  roc_annualized: number | null
  otm_pct: number
  is_em_strike: boolean
  iv_fallback: boolean
  stale_premium: boolean
}

export interface EmRankResult {
  symbol: string
  price: number
  bb_upper: number
  bb_middle: number
  bb_lower: number
  sma_ratio: number
  rsi: number
  iv_rank: number | null
  iv_percentile: number | null
  earnings_date: string | null
  earnings_within_dte: boolean
  vol_support_126_1: number | null
  vol_support_126_2: number | null
  vol_support_126_3: number | null
  dte: number
  expiration: string
  expected_move: number
  chain_median_oi: number
  dist_from_52w_high_pct: number
  iv_hv_ratio: number | null
  strikes: EmRankStrikeInfo[]
  best_roc: number
  using_hv_fallback: boolean
}

export interface EmRankError {
  symbol: string
  reason: string
}

export interface EmRankExpirationRow {
  dte: number
  expiration: string
  earnings_within_dte: boolean
  expected_move: number
  chain_median_oi: number
  strikes: EmRankStrikeInfo[]
  best_roc: number
  using_hv_fallback: boolean
}

export interface GroupedEmRankResult {
  symbol: string
  price: number
  bb_upper: number
  bb_middle: number
  bb_lower: number
  sma_ratio: number
  rsi: number
  iv_rank: number | null
  iv_percentile: number | null
  earnings_date: string | null
  earnings_within_dte: boolean
  vol_support_126_1: number | null
  vol_support_126_2: number | null
  vol_support_126_3: number | null
  dist_from_52w_high_pct: number
  iv_hv_ratio: number | null
  best_roc: number
  using_hv_fallback: boolean
  expirations: EmRankExpirationRow[]
}

export interface EmRankRequest {
  symbols: string[]
  minDTE: number
  maxDTE: number
  maxCapital?: number
}
