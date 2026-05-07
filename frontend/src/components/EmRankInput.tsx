import { useState, useRef, KeyboardEvent } from 'react'
import { UNIVERSE_OPTIONS, DEFAULT_UNIVERSE, universeSize, type UniverseKey } from '../constants/universes'

interface Props {
  onScan: (topN: number, minDTE: number, maxDTE: number, universe: UniverseKey, maxCapital?: number) => void
  onCustom: (symbols: string[], minDTE: number, maxDTE: number, maxCapital?: number) => void
  loading: boolean
}

export function EmRankInput({ onScan, onCustom, loading }: Props) {
  const [mode, setMode] = useState<'scan' | 'custom'>('scan')

  // Scan mode state
  const [topN, setTopN] = useState(20)
  const [scanMinDTE, setScanMinDTE] = useState(30)
  const [scanMaxDTE, setScanMaxDTE] = useState(60)
  const [universe, setUniverse] = useState<UniverseKey>(DEFAULT_UNIVERSE)
  const [scanMaxCapital, setScanMaxCapital] = useState<number | ''>('')

  // Custom mode state
  const [chips, setChips] = useState<string[]>([])
  const [inputValue, setInputValue] = useState('')
  const [minDTE, setMinDTE] = useState(30)
  const [maxDTE, setMaxDTE] = useState(60)
  const [dteError, setDteError] = useState<string | null>(null)
  const [maxCapital, setMaxCapital] = useState<number | ''>('')
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

  function handleScan() {
    if (scanMinDTE > scanMaxDTE) return
    if (scanMaxCapital !== '' && (!Number.isFinite(scanMaxCapital) || scanMaxCapital < 100)) return
    onScan(
      topN,
      scanMinDTE,
      scanMaxDTE,
      universe,
      scanMaxCapital !== '' && Number.isFinite(scanMaxCapital) ? scanMaxCapital : undefined,
    )
  }

  function handleCustomSubmit() {
    let err: string | null = null
    if (minDTE > maxDTE) err = 'Min DTE must be ≤ Max DTE'
    else if (minDTE < 1 || maxDTE > 90) err = 'DTE must be between 1 and 90'
    else if (maxCapital !== '' && (!Number.isFinite(maxCapital) || maxCapital < 100))
      err = 'Max Capital must be at least $100'
    setDteError(err)
    if (err) return

    const allSymbols = inputValue.trim()
      ? [...chips, ...inputValue.split(/[\s,]+/).filter(Boolean)]
      : chips
    const unique = [...new Set(allSymbols.map(s => s.trim().toUpperCase()).filter(Boolean))]
    if (unique.length === 0) return
    onCustom(
      unique.slice(0, 20),
      minDTE,
      maxDTE,
      maxCapital !== '' && Number.isFinite(maxCapital) ? maxCapital : undefined,
    )
  }

  return (
    <div className="symbol-input-panel">
      <div className="momentum-mode-toggle">
        <button
          className={`mode-btn${mode === 'custom' ? ' mode-btn-active' : ''}`}
          onClick={() => setMode('custom')}
          disabled={loading}
        >
          Custom Symbols
        </button>
        <button
          className={`mode-btn${mode === 'scan' ? ' mode-btn-active' : ''}`}
          onClick={() => setMode('scan')}
          disabled={loading}
        >
          ⚡ Auto Scan
        </button>
      </div>

      {mode === 'scan' && (
        <div className="momentum-scan-row">
          <div className="momentum-scan-info">
            <span className="scan-desc">
              Scans <strong>{universeSize(universe)}</strong> stocks — {UNIVERSE_OPTIONS.find(o => o.key === universe)?.hint}
            </span>
            <span className="app-subtitle">Ranked by annualized ROC at the 1σ EM strike — no scoring</span>
          </div>
          <div className="momentum-scan-controls">
            <label className="filter-item">
              Universe
              <select
                className="filter-select"
                value={universe}
                onChange={e => setUniverse(e.target.value as UniverseKey)}
                disabled={loading}
              >
                {UNIVERSE_OPTIONS.map(o => (
                  <option key={o.key} value={o.key}>{o.label}</option>
                ))}
              </select>
            </label>
            <label className="filter-item">
              Top
              <input
                type="number"
                className="filter-number"
                value={topN}
                min={5}
                max={50}
                step={5}
                onChange={e => setTopN(Number(e.target.value))}
                disabled={loading}
              />
              results
            </label>
            <label className="filter-item">
              Min DTE
              <input
                type="number"
                className="dte-input"
                value={scanMinDTE}
                min={1}
                max={90}
                onChange={e => setScanMinDTE(Number(e.target.value))}
                disabled={loading}
              />
            </label>
            <label className="filter-item">
              Max DTE
              <input
                type="number"
                className="dte-input"
                value={scanMaxDTE}
                min={1}
                max={90}
                onChange={e => setScanMaxDTE(Number(e.target.value))}
                disabled={loading}
              />
            </label>
            <label className="filter-item">
              Max Capital ($)
              <input
                type="number"
                className="dte-input"
                value={scanMaxCapital}
                placeholder="No limit"
                min={100}
                step={100}
                onChange={e => setScanMaxCapital(e.target.value === '' ? '' : Number(e.target.value))}
                disabled={loading}
              />
            </label>
            <button
              className="btn btn-primary"
              onClick={handleScan}
              disabled={loading || scanMinDTE > scanMaxDTE}
            >
              {loading ? 'Scanning…' : '⚡ Scan Now'}
            </button>
          </div>
        </div>
      )}

      {mode === 'custom' && (
        <>
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

            <div className="dte-controls">
              <label>
                Min DTE
                <input
                  type="number"
                  className="dte-input"
                  value={minDTE}
                  min={1}
                  max={90}
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
                  max={90}
                  onChange={e => setMaxDTE(Number(e.target.value))}
                  disabled={loading}
                />
              </label>
              <label>
                Max Capital ($)
                <input
                  type="number"
                  className="dte-input"
                  value={maxCapital}
                  placeholder="No limit"
                  min={100}
                  step={100}
                  onChange={e => setMaxCapital(e.target.value === '' ? '' : Number(e.target.value))}
                  disabled={loading}
                />
              </label>
            </div>

            <button
              className="btn btn-primary"
              onClick={handleCustomSubmit}
              disabled={loading || (chips.length === 0 && !inputValue.trim())}
            >
              {loading ? 'Running…' : 'Run EM Rank'}
            </button>
          </div>
          {dteError && <div className="dte-error">{dteError}</div>}
        </>
      )}
    </div>
  )
}
