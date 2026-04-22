import { useState } from 'react'
import type { MomentumRequest, MomentumResponse, MomentumResult, MomentumError } from '../types/momentum'

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

interface UseMomentumReturn {
  results: MomentumResult[]
  errors: MomentumError[]
  loading: boolean
  symbolCount: number
  isScanMode: boolean
  errorMessage: string | null
  run: (req: MomentumRequest) => Promise<void>
  scan: (topN?: number) => Promise<void>
}

export function useMomentum(): UseMomentumReturn {
  const [results, setResults] = useState<MomentumResult[]>([])
  const [errors, setErrors] = useState<MomentumError[]>([])
  const [loading, setLoading] = useState(false)
  const [symbolCount, setSymbolCount] = useState(0)
  const [isScanMode, setIsScanMode] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  async function run(req: MomentumRequest) {
    setLoading(true)
    setIsScanMode(false)
    setErrorMessage(null)
    setResults([])
    setErrors([])
    setSymbolCount(req.symbols.length)

    try {
      const response = await fetch(`${API_BASE}/api/screener/momentum`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(req),
      })

      if (!response.ok) {
        let detail = `Server error ${response.status}`
        try {
          const body = await response.json()
          if (body?.detail) {
            detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
          }
        } catch { /* ignore */ }
        setErrorMessage(detail)
        return
      }

      const data: MomentumResponse = await response.json()
      setResults(data.results)
      setErrors(data.errors)
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Network error — is the backend running?')
    } finally {
      setLoading(false)
    }
  }

  async function scan(topN: number = 20) {
    setLoading(true)
    setIsScanMode(true)
    setErrorMessage(null)
    setResults([])
    setErrors([])
    setSymbolCount(0)

    try {
      const response = await fetch(`${API_BASE}/api/screener/momentum/scan?top_n=${topN}`, {
        method: 'GET',
      })

      if (!response.ok) {
        let detail = `Server error ${response.status}`
        try {
          const body = await response.json()
          if (body?.detail) {
            detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
          }
        } catch { /* ignore */ }
        setErrorMessage(detail)
        return
      }

      const data: MomentumResponse = await response.json()
      setResults(data.results)
      setErrors(data.errors)
    } catch (err: unknown) {
      setErrorMessage(err instanceof Error ? err.message : 'Network error — is the backend running?')
    } finally {
      setLoading(false)
    }
  }

  return { results, errors, loading, symbolCount, isScanMode, errorMessage, run, scan }
}
