import { useState, useRef, KeyboardEvent } from 'react'

const PRESET_BASKET = ['AAPL', 'MSFT', 'NVDA', 'TSLA', 'AMZN', 'META', 'GOOGL', 'SPY', 'QQQ', 'AMD']

interface Props {
  onSubmit: (symbols: string[], minDTE: number, maxDTE: number) => void
  loading: boolean
  defaultMinDTE?: number
  defaultMaxDTE?: number
  maxDteLimit?: number
  showDTE?: boolean
}

export function SymbolInput({ onSubmit, loading, defaultMinDTE = 30, defaultMaxDTE = 45, maxDteLimit = 90, showDTE = true }: Props) {
  const [chips, setChips] = useState<string[]>([])
  const [inputValue, setInputValue] = useState('')
  const [minDTE, setMinDTE] = useState(defaultMinDTE)
  const [maxDTE, setMaxDTE] = useState(defaultMaxDTE)
  const [dteError, setDteError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  function addSymbol(raw: string) {
    const sym = raw.trim().toUpperCase().replace(/[^A-Z0-9]/g, '')
    if (!sym || sym.length > 10) return
    if (chips.includes(sym)) return
    if (chips.length >= 20) return
    setChips(prev => [...prev, sym])
  }

  function removeChip(sym: string) {
    setChips(prev => prev.filter(s => s !== sym))
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      addSymbol(inputValue)
      setInputValue('')
    } else if (e.key === 'Backspace' && inputValue === '' && chips.length > 0) {
      setChips(prev => prev.slice(0, -1))
    }
  }

  function handleBlur() {
    if (inputValue.trim()) {
      addSymbol(inputValue)
      setInputValue('')
    }
  }

  function loadPreset() {
    setChips(PRESET_BASKET)
  }

  function handleSubmit() {
    if (showDTE) {
      let err: string | null = null
      if (minDTE > maxDTE) err = 'Min DTE must be ≤ Max DTE'
      else if (minDTE < 1 || maxDTE > maxDteLimit) err = `DTE must be between 1 and ${maxDteLimit}`
      setDteError(err)
      if (err) return
    }

    const allSymbols = inputValue.trim()
      ? [...chips, ...inputValue.split(/[\s,]+/).filter(Boolean)]
      : chips
    const unique = [...new Set(allSymbols.map(s => s.trim().toUpperCase()).filter(Boolean))]
    if (unique.length === 0) return
    onSubmit(unique.slice(0, 20), minDTE, maxDTE)
  }

  return (
    <div className="symbol-input-panel">
      <div className="symbol-input-row">
        <div
          className="chip-container"
          onClick={() => inputRef.current?.focus()}
        >
          {chips.map(sym => (
            <span key={sym} className="chip">
              {sym}
              <button
                className="chip-remove"
                onClick={e => { e.stopPropagation(); removeChip(sym) }}
                aria-label={`Remove ${sym}`}
              >
                ×
              </button>
            </span>
          ))}
          <input
            ref={inputRef}
            className="chip-input"
            value={inputValue}
            onChange={e => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={handleBlur}
            placeholder={chips.length === 0 ? 'Type symbols (e.g. AAPL, MSFT)…' : ''}
            disabled={loading}
          />
        </div>

        {showDTE && (
          <div className="dte-controls">
            <label>
              Min DTE
              <input
                type="number"
                className="dte-input"
                value={minDTE}
                min={1}
                max={maxDteLimit}
                onChange={e => setMinDTE(Number(e.target.value))}
                disabled={loading}
              />
            </label>
            <label>
              Max DTE
              <input
                type="number"
                className="dte-input"
                value={maxDTE}
                min={1}
                max={maxDteLimit}
                onChange={e => setMaxDTE(Number(e.target.value))}
                disabled={loading}
              />
            </label>
          </div>
        )}

        <button className="btn btn-secondary" onClick={loadPreset} disabled={loading}>
          Load Preset
        </button>
        <button
          className="btn btn-primary"
          onClick={handleSubmit}
          disabled={loading || (chips.length === 0 && !inputValue.trim())}
        >
          {loading ? 'Running…' : 'Run Screener'}
        </button>
      </div>

      {showDTE && dteError && <p className="dte-error">{dteError}</p>}

      <p className="symbol-hint">
        Press Enter or comma to add a symbol. Max 20 symbols.{' '}
        {chips.length > 0 && <span>{chips.length}/20 loaded.</span>}
      </p>
    </div>
  )
}
