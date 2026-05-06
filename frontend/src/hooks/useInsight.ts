import { useCallback, useState } from 'react'
import type { InsightRequest, InsightResult } from '../types/insight'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

interface UseInsightReturn {
  insights: Map<string, InsightResult>
  loading: Set<string>
  errors: Map<string, string>
  fetchInsight: (key: string, request: InsightRequest) => Promise<void>
}

export function useInsight(): UseInsightReturn {
  const [insights, setInsights] = useState<Map<string, InsightResult>>(new Map())
  const [loading, setLoading] = useState<Set<string>>(new Set())
  const [errors, setErrors] = useState<Map<string, string>>(new Map())

  const fetchInsight = useCallback(async (key: string, request: InsightRequest) => {
    setLoading(prev => new Set(prev).add(key))
    setErrors(prev => { const m = new Map(prev); m.delete(key); return m })

    try {
      const res = await fetch(`${API_BASE}/api/screener/csp/insight`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: 'Request failed' }))
        throw new Error(body.detail ?? 'Request failed')
      }
      const data: InsightResult = await res.json()
      setInsights(prev => { const m = new Map(prev); m.set(key, data); return m })
    } catch (e) {
      setErrors(prev => { const m = new Map(prev); m.set(key, String(e)); return m })
    } finally {
      setLoading(prev => { const s = new Set(prev); s.delete(key); return s })
    }
  }, [])

  return { insights, loading, errors, fetchInsight }
}
