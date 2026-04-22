export interface MomentumResult {
  symbol: string
  price: number
  price_change_1d_pct: number | null
  rvol: number | null
  rsi: number | null
  roc_21: number | null
  sma_ratio: number | null
  sma20_slope_pct: number | null
  price_vs_sma20_pct: number | null
  dist_from_52w_high_pct: number | null
  dist_from_sma200_pct: number | null
  macd_histogram: number | null
  high_52w: number
  low_52w: number
  short_ratio: number | null
  momentum_score: number
}

export interface MomentumError {
  symbol: string
  reason: string
}

export interface MomentumRequest {
  symbols: string[]
}

export interface MomentumResponse {
  results: MomentumResult[]
  errors: MomentumError[]
}

export interface MomentumFilterState {
  minScore: number
  minRvol: number
  minRsi: number
  maxRsi: number
  minRoc21: number
  smaRatioBullishOnly: boolean
  maxDistFrom52wHigh: number   // e.g. 15 means within 15% of 52w high (0 = off)
}
