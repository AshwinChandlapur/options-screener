import { useState } from 'react'
import type { EtvData, EtvHorizon, EtvRiskTolerance } from '../types/etv'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

export function useEtv() {
  const [data, setData] = useState<EtvData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function fetchTicker(
    ticker: string,
    horizon: EtvHorizon = 'medium',
    risk: EtvRiskTolerance = 'moderate',
    refresh = false,
  ) {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({
        ticker,
        horizon,
        risk_tolerance: risk,
      })
      if (refresh) params.set('refresh', 'true')
      const url = `${API_BASE}/api/etv?${params.toString()}`
      const r = await fetch(url)
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: r.statusText }))
        throw new Error(err.detail ?? 'Request failed')
      }
      const json = (await r.json()) as EtvData
      setData(json)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
      setData(null)
    } finally {
      setLoading(false)
    }
  }

  return { data, loading, error, fetchTicker }
}
