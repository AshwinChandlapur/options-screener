import { useState, useRef, KeyboardEvent } from 'react'

const UNIVERSE_SIZE = 75  // keep in sync with backend/services/universe.py

const SCORE_LEGEND = [
  { factor: 'RVOL',             weight: 30, detail: '≥ 3× avg volume = max. Measures unusual buying activity.' },
  { factor: 'RSI(14)',          weight: 20, detail: '55–72 = ideal breakout zone (full). 50–55 or 72–80 = partial.' },
  { factor: '52-week proximity',weight: 25, detail: 'Closer to 52w high = higher score. >20% below high = 0.' },
  { factor: 'SMA50/200 ratio',  weight: 15, detail: '≥ 1.10 (SMA50 at least 10% above SMA200) = max.' },
  { factor: 'ROC(21)',          weight: 10, detail: '≥ +10% price change over last 21 days = max.' },
]

const SCORE_TIERS = [
  { range: '≥ 70', label: 'Strong',   color: '#4ade80', desc: 'All signals firing — high-probability momentum setup' },
  { range: '50–69', label: 'Moderate', color: '#facc15', desc: 'Most signals aligned, not at full strength' },
  { range: '< 50',  label: 'Weak',     color: '#f87171', desc: 'Mixed or absent signals — low conviction' },
]

interface Props {
  onScan: (topN: number) => void
  onCustom: (symbols: string[]) => void
  loading: boolean
}

export function MomentumInput({ onScan, onCustom, loading }: Props) {
  const [mode, setMode] = useState<'scan' | 'custom'>('scan')
  const [showLegend, setShowLegend] = useState(false)
  const [topN, setTopN] = useState(20)
  const [chips, setChips] = useState<string[]>([])
  const [inputValue, setInputValue] = useState('')
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

  function handleCustomSubmit() {
    const allSymbols = inputValue.trim()
      ? [...chips, ...inputValue.split(/[\s,]+/).filter(Boolean)]
      : chips
    const unique = [...new Set(allSymbols.map(s => s.trim().toUpperCase()).filter(Boolean))]
    if (unique.length === 0) return
    onCustom(unique.slice(0, 20))
  }

  return (
    <div className="symbol-input-panel">
      {/* Mode toggle */}
      <div className="momentum-mode-toggle">
        <button
          className={`mode-btn${mode === 'scan' ? ' mode-btn-active' : ''}`}
          onClick={() => setMode('scan')}
          disabled={loading}
        >
          ⚡ Auto Scan
        </button>
        <button
          className={`mode-btn${mode === 'custom' ? ' mode-btn-active' : ''}`}
          onClick={() => setMode('custom')}
          disabled={loading}
        >
          Custom Symbols
        </button>
        <button
          className="mode-btn score-legend-toggle"
          onClick={() => setShowLegend(v => !v)}
          title="How the momentum score is calculated"
        >
          {showLegend ? '▲ Score Guide' : '▼ Score Guide'}
        </button>
      </div>

      {showLegend && (
        <div className="score-legend">
          <div className="score-legend-tiers">
            {SCORE_TIERS.map(t => (
              <div key={t.range} className="score-tier">
                <span className="score-tier-badge" style={{ color: t.color }}>{t.label}</span>
                <span className="score-tier-range">{t.range}</span>
                <span className="score-tier-desc">{t.desc}</span>
              </div>
            ))}
          </div>
          <div className="score-legend-factors">
            <div className="score-legend-header">Score breakdown (total 100 pts)</div>
            {SCORE_LEGEND.map(f => (
              <div key={f.factor} className="score-factor-row">
                <span className="score-factor-name">{f.factor}</span>
                <span className="score-factor-weight">{f.weight} pts</span>
                <span className="score-factor-detail">{f.detail}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {mode === 'scan' && (
        <div className="momentum-scan-row">
          <div className="momentum-scan-info">
            <span className="scan-desc">
              Scans <strong>{UNIVERSE_SIZE}</strong> stocks across AI · Semis · Cloud · Fintech · Growth
            </span>
            <span className="app-subtitle">Ranked by composite momentum score — returns top candidates automatically</span>
          </div>
          <div className="momentum-scan-controls">
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
            <button
              className="btn btn-primary"
              onClick={() => onScan(topN)}
              disabled={loading}
            >
              {loading ? 'Scanning…' : `⚡ Scan Now`}
            </button>
          </div>
        </div>
      )}

      {mode === 'custom' && (
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
              placeholder={chips.length === 0 ? 'Type symbols (e.g. NVDA, MRVL)…' : ''}
              disabled={loading}
            />
          </div>
          <button
            className="btn btn-primary"
            onClick={handleCustomSubmit}
            disabled={loading || (chips.length === 0 && !inputValue.trim())}
          >
            {loading ? 'Running…' : 'Run Screener'}
          </button>
        </div>
      )}
    </div>
  )
}
