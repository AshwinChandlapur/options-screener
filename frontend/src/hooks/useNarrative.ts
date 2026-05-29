import { useCallback, useEffect, useRef, useState } from 'react'
import type { AcsScore, NarrativeAlert, NarrativeError, TickerDetail } from '../types/narrative'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'
const REFRESH_INTERVAL_MS = 5 * 60 * 1000  // 5 min

interface UseNarrativeReturn {
  top: AcsScore[]
  emerging: AcsScore[]
  alerts: NarrativeAlert[]
  loading: boolean
  error: NarrativeError | null
  lastUpdatedAt: Date | null
  /** When the backend's scoreboard cache was last computed by the scorer
   *  job. Reflects *data* freshness, not network freshness. Read from the
   *  `X-Scoreboard-Computed-At` response header; null on cold start. */
  scoreboardAsOf: Date | null
  refresh: () => Promise<void>
  fetchDetail: (ticker: string) => Promise<{ data: TickerDetail | null; error: NarrativeError | null }>
}

async function safeFetch<T>(
  url: string,
): Promise<{ data: T | null; error: NarrativeError | null; headers: Headers | null }> {
  try {
    const response = await fetch(url, { method: 'GET' })
    if (response.status === 503) {
      let detail = 'Narrative platform not yet provisioned.'
      try {
        const body = await response.json()
        if (typeof body?.detail === 'string') detail = body.detail
      } catch { /* ignore */ }
      return { data: null, error: { detail, unavailable: true }, headers: null }
    }
    if (!response.ok) {
      return {
        data: null,
        error: { detail: `Server error ${response.status}`, unavailable: false },
        headers: null,
      }
    }
    const data = await response.json() as T
    return { data, error: null, headers: response.headers }
  } catch (err: unknown) {
    const detail = err instanceof Error ? err.message : 'Network error — is the backend running?'
    return { data: null, error: { detail, unavailable: false }, headers: null }
  }
}

export function useNarrative(): UseNarrativeReturn {
  const [top, setTop] = useState<AcsScore[]>([])
  const [emerging, setEmerging] = useState<AcsScore[]>([])
  const [alerts, setAlerts] = useState<NarrativeAlert[]>([])
  // Start true so tables never flash "No scores yet." before the first fetch.
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<NarrativeError | null>(null)
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null)
  const [scoreboardAsOf, setScoreboardAsOf] = useState<Date | null>(null)
  const intervalRef = useRef<number | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    const [topRes, emergingRes, alertsRes] = await Promise.all([
      safeFetch<AcsScore[]>(`${API_BASE}/api/narrative/tickers/top?limit=50`),
      safeFetch<AcsScore[]>(`${API_BASE}/api/narrative/emerging?limit=50`),
      safeFetch<NarrativeAlert[]>(`${API_BASE}/api/narrative/alerts?limit=50`),
    ])
    // Alerts is Phase 7 (not yet implemented — always returns 503). Exclude it
    // from the data-error check so its failure does not poison the banner or
    // prevent lastUpdatedAt from updating when top/emerging are healthy.
    const dataError = topRes.error ?? emergingRes.error
    if (dataError) setError(dataError)
    // Only replace data when the request succeeded — preserve the previous rows
    // on transient network errors so the UI does not flash empty on auto-refresh.
    if (topRes.data !== null) setTop(topRes.data)
    if (emergingRes.data !== null) setEmerging(emergingRes.data)
    if (alertsRes.data !== null) setAlerts(alertsRes.data)
    if (!dataError) setLastUpdatedAt(new Date())
    // Prefer the X-Scoreboard-Computed-At header from whichever endpoint
    // succeeded — both should agree (single shared cache doc).
    const computedHeader =
      emergingRes.headers?.get('X-Scoreboard-Computed-At') ??
      topRes.headers?.get('X-Scoreboard-Computed-At') ??
      null
    if (computedHeader) {
      const parsed = new Date(computedHeader)
      if (!Number.isNaN(parsed.getTime())) setScoreboardAsOf(parsed)
    }
    setLoading(false)
  }, [])

  const fetchDetail = useCallback(
    (ticker: string) =>
      safeFetch<TickerDetail>(
        `${API_BASE}/api/narrative/tickers/${encodeURIComponent(ticker.toUpperCase())}/detail`,
      ),
    [],
  )

  useEffect(() => {
    void refresh()
    intervalRef.current = window.setInterval(() => {
      void refresh()
    }, REFRESH_INTERVAL_MS)
    return () => {
      if (intervalRef.current != null) window.clearInterval(intervalRef.current)
    }
  }, [refresh])

  return { top, emerging, alerts, loading, error, lastUpdatedAt, scoreboardAsOf, refresh, fetchDetail }
}
