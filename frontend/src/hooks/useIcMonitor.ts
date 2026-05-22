import { useCallback, useEffect, useRef, useState } from 'react'
import type { IcMonitor, NarrativeError } from '../types/narrative'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'
// Refresh every 6 hours — IC values change at most once a day.
const REFRESH_INTERVAL_MS = 6 * 60 * 60 * 1000

interface UseIcMonitorReturn {
  data: IcMonitor | null
  loading: boolean
  error: NarrativeError | null
  lastUpdatedAt: Date | null
  refresh: () => Promise<void>
}

export function useIcMonitor(): UseIcMonitorReturn {
  const [data, setData] = useState<IcMonitor | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<NarrativeError | null>(null)
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null)
  const intervalRef = useRef<number | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await fetch(`${API_BASE}/api/narrative/ic-monitor`)
      if (response.status === 503) {
        let detail = 'IC monitor not yet available.'
        try {
          const body = await response.json()
          if (typeof body?.detail === 'string') detail = body.detail
        } catch { /* ignore */ }
        setError({ detail, unavailable: true })
        return
      }
      if (!response.ok) {
        setError({ detail: `Server error ${response.status}`, unavailable: false })
        return
      }
      const json = await response.json() as IcMonitor
      setData(json)
      setLastUpdatedAt(new Date())
      setError(null)
    } catch (err: unknown) {
      const detail = err instanceof Error ? err.message : 'Network error'
      setError({ detail, unavailable: false })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    intervalRef.current = window.setInterval(refresh, REFRESH_INTERVAL_MS)
    return () => {
      if (intervalRef.current !== null) window.clearInterval(intervalRef.current)
    }
  }, [refresh])

  return { data, loading, error, lastUpdatedAt, refresh }
}
