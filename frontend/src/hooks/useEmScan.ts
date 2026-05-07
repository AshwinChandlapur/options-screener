import { useEffect, useState } from 'react'
import type { EmRankRequest, EmRankResult, EmRankError } from '../types/emRank'
import { loadResultCache, saveResultCache, clearResultCache } from '../utils/resultCache'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

interface UseEmScanReturn {
  results: EmRankResult[]
  errors: EmRankError[]
  loading: boolean
  symbolCount: number
  isScanMode: boolean
  errorMessage: string | null
  cachedAt: number | null
  run: (req: EmRankRequest) => Promise<void>
  scan: (topN?: number, minDTE?: number, maxDTE?: number, universe?: string, maxCapital?: number) => Promise<void>
}

export function useEmScan(): UseEmScanReturn {
  const [results, setResults] = useState<EmRankResult[]>([])
  const [errors, setErrors] = useState<EmRankError[]>([])
  const [loading, setLoading] = useState(false)
  const [symbolCount, setSymbolCount] = useState(0)
  const [isScanMode, setIsScanMode] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [cachedAt, setCachedAt] = useState<number | null>(null)

  useEffect(() => {
    const entry = loadResultCache<{ results: EmRankResult[]; errors: EmRankError[] }>('em-rank')
    if (entry) {
      setResults(entry.data.results)
      setErrors(entry.data.errors)
      setCachedAt(entry.savedAt)
    }
  }, [])

  async function run(req: EmRankRequest) {
    setLoading(true)
    setIsScanMode(false)
    setErrorMessage(null)
    setCachedAt(null)
    setResults([])
    setErrors([])
    clearResultCache('em-rank')
    setSymbolCount(req.symbols.length)

    try {
      const response = await fetch(`${API_BASE}/api/screener/csp/em-rank`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(req),
      })

      if (!response.ok) {
        let detail = `Server error ${response.status}`
        try {
          const body = await response.json()
          if (body?.detail) {
            detail = typeof body.detail === 'string'
              ? body.detail
              : JSON.stringify(body.detail)
          }
        } catch {
          // ignore parse errors
        }
        setErrorMessage(detail)
        return
      }

      const data = await response.json()
      setResults(data.results ?? [])
      setErrors(data.errors ?? [])
      saveResultCache('em-rank', { results: data.results ?? [], errors: data.errors ?? [] })
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Network error')
    } finally {
      setLoading(false)
    }
  }

  async function scan(
    topN = 20,
    minDTE = 30,
    maxDTE = 60,
    universe = 'all',
    maxCapital?: number,
  ) {
    setLoading(true)
    setIsScanMode(true)
    setErrorMessage(null)
    setCachedAt(null)
    setResults([])
    setErrors([])
    clearResultCache('em-rank')
    setSymbolCount(0)

    try {
      const params = new URLSearchParams({
        top_n: String(topN),
        min_dte: String(minDTE),
        max_dte: String(maxDTE),
        universe,
        ...(maxCapital !== undefined ? { max_capital: String(maxCapital) } : {}),
      })

      const response = await fetch(`${API_BASE}/api/screener/csp/em-scan?${params}`)

      if (!response.ok) {
        let detail = `Server error ${response.status}`
        try {
          const body = await response.json()
          if (body?.detail) {
            detail = typeof body.detail === 'string'
              ? body.detail
              : JSON.stringify(body.detail)
          }
        } catch {
          // ignore parse errors
        }
        setErrorMessage(detail)
        return
      }

      const data = await response.json()
      setResults(data.results ?? [])
      setErrors(data.errors ?? [])
      saveResultCache('em-rank', { results: data.results ?? [], errors: data.errors ?? [] })
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Network error')
    } finally {
      setLoading(false)
    }
  }

  return {
    results,
    errors,
    loading,
    symbolCount,
    isScanMode,
    errorMessage,
    cachedAt,
    run,
    scan,
  }
}
