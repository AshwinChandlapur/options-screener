import { useState, useRef } from 'react'
import type { KeyboardEvent } from 'react'

const UNIVERSE_SIZE = 75

const SCORE_LEGEND = [
  { factor: '— ENV SCORE (×0.35) —', weight: null, detail: '', definition: '', why: '', formula: '' },
  { factor: 'IV / HV Ratio (inv.)', weight: 45, detail: '<0.7=45 · 0.7–0.9→27 · 0.9–1.1→13 · 1.1–1.5→2 · ≥1.5=0.',
    definition: 'Implied Volatility divided by 30-day realized (Historical) Volatility — inverted for DITM buyers. A ratio below 1.0 means the market is pricing in less movement than the stock actually delivers, making options cheap relative to realized movement.',
    why: 'The sole IV metric — measures buyer\'s edge relative to realized vol. IV < HV means the market is pricing in LESS movement than the stock actually delivers. IV Rank was removed to avoid double-counting; IV/HV is more statistically precise and directly actionable.',
    formula: 'iv_hv_ratio = yfinance_IV / HV_30d\n  HV_30d = std(log(Closeₜ / Closeₜ₋₁), 30d) × √252\n  INVERTED: ratio < 1.0 = IV cheaper than realized vol\n  Used directly in earnings penalty: high IVR softens penalty' },
  { factor: 'Trend Strength',       weight: 30, detail: 'SMA Align(15) + SMA50 Slope(7) + 52W Prox(8).',
    definition: 'A composite of three trend signals: SMA alignment (are price, SMA50, and SMA200 in bullish order?), SMA50 slope (is the trend accelerating or stalling?), and 52-week proximity (how close is price to its annual high?).',
    why: 'Composite replacing the old SMA Alignment + 52W Distance split. Three independent signals: alignment (direction), SMA50 slope (momentum of the trend), and 52W proximity (strength). A stock can be in alignment but with a flattening SMA50 — the slope catches that deterioration earlier.',
    formula: 'SMA Alignment: Price>SMA50>SMA200=15 · Price>SMA50=9 · SMA50>SMA200=4\n  SMA50 Slope: pct change in SMA50 over 10 days → >1%=7 · >0.3%=5+ · >0%=2+ · <-0.5%=0\n  52W Proximity: ≤5%=8 · ≤15%→3 · ≤30%→0' },
  { factor: 'Trend Persistence',    weight: 10, detail: '≥75%=10 · ≥60%→6 · ≥50%→3 · ≥40%=1 · <40%=0.',
    definition: 'The percentage of the last 60 trading sessions in which the stock closed above its 50-day SMA. A high value means the uptrend has been consistently maintained, not just a recent bounce.',
    why: 'Replaces RSI(14) for LEAPS. RSI reacts to 2–3 week swings, which is noise for a 180–365 DTE position. Trend persistence measures what % of the last 60 sessions the stock closed above its SMA50 — directly relevant to whether the uptrend will persist over your holding period.',
    formula: '% of last 60 sessions where Close > SMA50\n  ≥75% = stock reliably above trend\n  <40% = choppy/downtrend, avoid' },
  { factor: 'Chain Median OI',      weight: 10, detail: 'log₁₀ scale · log₁₀(OI)/log₁₀(5000) × 10.',
    definition: 'The median open interest across deep ITM call strikes in the 0.65–0.95 delta range. Open interest is the total number of outstanding contracts at those strikes — a measure of how actively traded the DITM chain is.',
    why: 'Deep ITM calls are illiquid by nature. Minimum chain OI confirms a real market exists, enabling a fair entry and an exit when you want to close or roll the position.',
    formula: 'Filters to 0.65 < delta < 0.95 (DITM call range)\n  chain_median_oi = np.median([oi for candidates])\n  pts = min(log10(OI) / log10(5000), 1.0) × 10' },
  { factor: 'Earnings Proximity',   weight: -15, detail: '<14d=−15/−8 · 14–30d=−8/−4 · 30–60d=−3/−1 · >60d=0.',
    definition: 'A tiered penalty based on how many calendar days remain until the next earnings announcement, softened when IV Rank is already above 50 (meaning the market has already priced in earnings uncertainty).',
    why: 'Tiered by calendar proximity AND softened when IV Rank >50 (earnings already priced in). The left value is for IVR ≤50, right for IVR >50. Immediate earnings (<14 days) are always penalized heavily — gap-down risk destroys intrinsic value regardless of IV.',
    formula: 'days_to_earnings = (earnings_date − today).days\n  IVR ≤ 50 : < 14d→−15 · 14–30d→−8 · 30–60d→−3\n  IVR > 50 : < 14d→−8  · 14–30d→−4 · 30–60d→−1\n  > 60 days → 0 (no penalty)' },
  { factor: '— STRIKE SCORE (×0.65) —', weight: null, detail: '', definition: '', why: '', formula: '' },
  { factor: 'Delta',                weight: 35, detail: '0.80–0.85=35 · ±band=28 · further out=18/9 · <0.65=0.',
    definition: 'The rate of change of the option price per $1 move in the stock. For a deep ITM call, delta near 0.80–0.85 means the option moves $0.80–$0.85 for every $1 the stock moves — capturing most of the upside while spending less than the full share price.',
    why: 'Delta 0.80–0.85 is the DITM sweet spot: 80–85% correlation to stock movement while paying less than 100% of the stock price. Moneyness% was removed — it is mathematically derived from delta for a given IV/expiry and was double-counting this same information.',
    formula: 'Black-Scholes call delta:\n  d1 = (ln(S/K) + (r + 0.5σ²)T) / (σ√T)\n  call_delta = N(d1)\n  σ = yfinance IV; falls back to HV_30d if IV < 15%' },
  { factor: 'Extrinsic %',          weight: 35, detail: '≤1%=35 · ≤2%→26 · ≤4%→14 · ≤6%→5 · ≤9%→0 · >9%=0.',
    definition: 'The time premium embedded in the option price above its intrinsic value (how much the stock is already above the strike), expressed as a percentage of the stock price. This portion decays to zero by expiration regardless of stock direction.',
    why: 'Extrinsic value is the time premium you pay that will DECAY to zero by expiration regardless of stock direction. Every dollar of extrinsic is a sunk cost. This is the core efficiency metric of DITM buying — minimizing what you pay above intrinsic value.',
    formula: 'intrinsic = max(0, price − strike)\n  extrinsic = max(0, premium − intrinsic)\n  extrinsic_pct = extrinsic / stock_price × 100\n  Normalized by stock price, not premium — comparable across price levels' },
  { factor: 'Bid-Ask Spread',       weight: 20, detail: '≤1%=20 · ≤3%→13 · ≤5%→7 · ≤8%→2 · >12%=0.',
    definition: 'The percentage difference between the ask and bid prices relative to the option midpoint: (ask − bid) / mid × 100. Lower means a tighter market and cheaper execution cost to enter and exit.',
    why: 'Deep ITM calls are notoriously illiquid — spreads of 5–15% on the premium are common. A wide spread costs you on entry AND exit. For a position held for months, spread quality compounds in importance. Weight raised to 20 to reflect this.',
    formula: 'spread_pct = (ask − bid) / mid × 100\n  Per-strike bid/ask from yfinance call chain' },
  { factor: 'OI / Volume',          weight: 10, detail: '≥500=10 · ≥200→6 · ≥100→3 · ≥50→1 · <50=0.',
    definition: 'Open interest (total outstanding contracts, used when market is closed) or today\'s volume (used when market is open) at this specific deep ITM strike — a direct count of active participants.',
    why: 'Open interest at this specific deep strike. Low OI on deep ITM calls means you may be the only participant. Closing a position at mid becomes difficult — you face the full spread on exit.',
    formula: 'Uses volume if US market is open (9:30–16:00 ET weekday)\n  Otherwise uses openInterest at this specific call strike' },
]

const SCORE_TIERS = [
  { range: '≥ 70', label: 'Strong',   color: '#4ade80', desc: 'Cheap IV environment + efficient deep strike + liquid chain — high-conviction DITM entry' },
  { range: '45–69', label: 'Moderate', color: '#facc15', desc: 'Acceptable setup; some IV cost or spread friction — manageable with good execution' },
  { range: '< 45',  label: 'Weak',     color: '#f87171', desc: 'Expensive options, high extrinsic, poor liquidity, or no uptrend — avoid' },
]

interface Props {
  onScan: (topN: number, minDTE: number, maxDTE: number) => void
  onCustom: (symbols: string[], minDTE: number, maxDTE: number) => void
  loading: boolean
}

export function DitmInput({ onScan, onCustom, loading }: Props) {
  const [mode, setMode] = useState<'scan' | 'custom'>('scan')
  const [showLegend, setShowLegend] = useState(false)
  const [expandedFactor, setExpandedFactor] = useState<string | null>(null)

  const [topN, setTopN] = useState(15)
  const [scanMinDTE, setScanMinDTE] = useState(180)
  const [scanMaxDTE, setScanMaxDTE] = useState(365)

  const [chips, setChips] = useState<string[]>([])
  const [inputValue, setInputValue] = useState('')
  const [minDTE, setMinDTE] = useState(180)
  const [maxDTE, setMaxDTE] = useState(365)
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

  function handleScan() {
    if (scanMinDTE > scanMaxDTE) return
    onScan(topN, scanMinDTE, scanMaxDTE)
  }

  function handleCustomSubmit() {
    let err: string | null = null
    if (minDTE > maxDTE) err = 'Min DTE must be ≤ Max DTE'
    else if (minDTE < 1 || maxDTE > 730) err = 'DTE must be between 1 and 730'
    setDteError(err)
    if (err) return

    const allSymbols = inputValue.trim()
      ? [...chips, ...inputValue.split(/[\s,]+/).filter(Boolean)]
      : chips
    const unique = [...new Set(allSymbols.map(s => s.trim().toUpperCase()).filter(Boolean))]
    if (unique.length === 0) return
    onCustom(unique.slice(0, 20), minDTE, maxDTE)
  }

  return (
    <div className="symbol-input-panel">
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
          title="How the DITM score is calculated"
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
            <div className="score-legend-header">Score breakdown — Final = 0.35 × Env + 0.65 × Strike</div>
            {SCORE_LEGEND.map(f => (
              f.weight === null
                ? <div key={f.factor} className="score-factor-section">{f.factor}</div>
                : <div key={f.factor} className="score-factor-block">
                    <div
                      className="score-factor-row score-factor-row-clickable"
                      onClick={() => setExpandedFactor(expandedFactor === f.factor ? null : f.factor)}
                    >
                      <span className="score-factor-expand">
                        {expandedFactor === f.factor ? '▾' : '▸'}
                      </span>
                      <span className="score-factor-name">{f.factor}</span>
                      <span
                        className="score-factor-weight"
                        style={{ color: f.weight < 0 ? '#f87171' : '#4ade80' }}
                      >
                        {f.weight > 0 ? `+${f.weight}` : f.weight} pts
                      </span>
                      <span className="score-factor-detail">{f.detail}</span>
                    </div>
                    {expandedFactor === f.factor && (f.definition || f.why || f.formula) && (
                      <div className="score-factor-expanded">
                        {f.definition && <p className="score-factor-definition"><strong>What</strong>{f.definition}</p>}
                        {f.why && <p className="score-factor-why"><strong>Why</strong>{f.why}</p>}
                        {f.formula && <pre className="score-factor-formula">{f.formula}</pre>}
                      </div>
                    )}
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
            <span className="app-subtitle">Ranked by DITM composite score — best cheap, deep, liquid calls</span>
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
            <label className="filter-item">
              Min DTE
              <input type="number" className="dte-input" value={scanMinDTE}
                min={1} max={730} onChange={e => setScanMinDTE(Number(e.target.value))} disabled={loading} />
            </label>
            <label className="filter-item">
              Max DTE
              <input type="number" className="dte-input" value={scanMaxDTE}
                min={1} max={730} onChange={e => setScanMaxDTE(Number(e.target.value))} disabled={loading} />
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
            <div className="chip-container" onClick={() => inputRef.current?.focus()}>
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
                <input type="number" className="dte-input" value={minDTE}
                  min={1} max={730} onChange={e => setMinDTE(Number(e.target.value))} />
              </label>
              <label>
                Max DTE
                <input type="number" className="dte-input" value={maxDTE}
                  min={1} max={730} onChange={e => setMaxDTE(Number(e.target.value))} />
              </label>
            </div>
            <button
              className="btn btn-primary"
              onClick={handleCustomSubmit}
              disabled={loading || chips.length === 0}
            >
              {loading ? 'Fetching…' : 'Run Screener'}
            </button>
          </div>
          {dteError && <div className="dte-error">{dteError}</div>}
        </>
      )}
    </div>
  )
}
