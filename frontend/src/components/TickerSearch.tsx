import { useState } from 'react'

interface TickerSearchProps {
  onSearch: (ticker: string) => void
  disabled?: boolean
}

const TICKER_RE = /^[A-Z][A-Z0-9.\-]{0,9}$/

/**
 * Compact ticker lookup input. Validates client-side against the same regex
 * the API enforces so we don't fire requests that will 422.
 */
export function TickerSearch({ onSearch, disabled }: TickerSearchProps) {
  const [value, setValue] = useState('')
  const [touched, setTouched] = useState(false)

  const normalized = value.trim().toUpperCase()
  const valid = TICKER_RE.test(normalized)

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    setTouched(true)
    if (!valid) return
    onSearch(normalized)
  }

  return (
    <form className="ticker-search" onSubmit={submit}>
      <input
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Look up ticker (e.g. NVDA)"
        maxLength={10}
        aria-label="Ticker symbol"
        disabled={disabled}
      />
      <button type="submit" disabled={disabled || !valid}>
        Search
      </button>
      {touched && !valid && normalized.length > 0 ? (
        <span className="ticker-search-error" role="alert">
          Invalid ticker
        </span>
      ) : null}
    </form>
  )
}
