export interface DitmResult {
  symbol: string
  price: number
  sma_ratio: number
  rsi: number
  iv_rank: number | null
  iv_percentile: number | null
  earnings_date: string | null
  earnings_within_dte: boolean
  strike: number
  strike_is_fallback: boolean
  expiration: string
  dte: number
  premium: number
  delta: number
  extrinsic_value: number
  extrinsic_pct: number
  moneyness_pct: number
  leverage_ratio: number
  breakeven_price: number
  breakeven_pct_above: number
  capital_at_risk: number
  vs_stock_cost_pct: number
  bid_ask_spread_pct: number | null
  open_interest: number | null
}

export interface DitmError {
  symbol: string
  reason: string
}

export interface DitmRequest {
  symbols: string[]
  minDTE: number
  maxDTE: number
  minDelta: number
}

export interface DitmResponse {
  results: DitmResult[]
  errors: DitmError[]
}

export interface DitmFilterState {
  minDelta: number
  maxExtrinsicPct: number
  minMoneynessPct: number
  minRsi: number
  maxRsi: number
  smaRatioBullishOnly: boolean
  maxSpreadPct: number
  excludeEarningsWithinDte: boolean
}
