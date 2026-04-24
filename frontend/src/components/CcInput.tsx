import { useState, useRef } from 'react'
import type { KeyboardEvent } from 'react'

const UNIVERSE_SIZE = 75

const SCORE_LEGEND = [
  { factor: '— ENV SCORE (×0.4) —', weight: null, detail: '', definition: '', why: '', formula: '' },
  { factor: 'HV Rank',         weight: 22,  detail: '<20=0 · 20–40 linear→6.6 · 40–60→13.2 · 60–80→18.3 · ≥80=22.',
    definition: 'A percentile (0–100) showing where today\'s 30-day historical volatility sits within its 252-day range. 100 = highest HV of the past year; 0 = lowest. Note: this is HV-derived, used as an IV proxy until true ATM IV history is available.',
    why: 'Sell premium when realized vol has been historically elevated — that condition typically coincides with rich option premium. High HV rank = expensive options → more credit collected for the same structural risk.',
    formula: 'Uses 30-day rolling HV ranked over 252 days.\n  hv_rank = (HV_today − HV_min_252) / (HV_max_252 − HV_min_252) × 100\n  HV = std(log(Closeₜ / Closeₜ₋₁), 30d) × √252' },
  { factor: 'IV / HV Ratio',   weight: 28,  detail: '<0.8=0 · 0.8–0.9→2.8 · 0.9–1.1→6.7 · 1.1–1.4→14 · 1.4–1.7→22.4 · ≥1.7=28. Stale IV (NaN or ≤0.01) → 0 + flag.',
    definition: 'Implied Volatility divided by 30-day realized (Historical) Volatility. Measures whether options are priced rich or cheap relative to actual recent movement in the stock.',
    why: "IV > HV means the market is pricing in more movement than the stock actually makes — the seller's edge. IV < HV = options are cheap; you'd be giving away premium below fair value.",
    formula: 'iv_hv_ratio = yfinance_IV / HV_30d\n  Stale-IV trigger: IV is NaN or ≤ 0.01 → IV/HV pts = 0 and row is flagged (was: silent fallback to 1.0)' },
  { factor: 'SMA Alignment',   weight: 15,  detail: 'Price>SMA50>SMA200=15 · Price>SMA50=9 · SMA50>SMA200=5.',
    definition: 'The relative ordering of price vs. its 50-day and 200-day Simple Moving Averages. All three in sequence (price > SMA50 > SMA200) is the textbook definition of a sustained uptrend.',
    why: 'An established uptrend means the underlying stock you own retains value while you collect call premium. Stocks in uptrends are less likely to collapse, protecting the shares you hold.',
    formula: 'SMA50  = rolling mean of Close over last 50 days\n  SMA200 = rolling mean of Close over last 200 days\n  Categorical: checks price > SMA50 and SMA50 > SMA200' },
  { factor: '52W High Dist.',  weight: 10,  detail: 'CC curve (consolidation): ≤5%=4 · 5–15%→4→10 · 15–25%→10→6 · 25–35%→6→2 · >35%=0.',
    definition: 'How far the current price is below its 52-week (252-trading-day) high, expressed as a percentage. For CCs the curve is direction-aware — moderate consolidation (5–15% below high) is preferred over near-high or deep-drawdown.',
    why: 'For CCs: stock at the 52W high has the most upside momentum and the highest risk of being called away. Modest consolidation 5–15% below the high gives the underlying room to drift sideways while premium decays. Far below the high signals a deeper drawdown that damages the underlying you hold.',
    formula: 'dist = (Closeₜ − max(Close, 252d)) / max(Close, 252d) × 100\n  pct_below = abs(min(dist, 0))\n  Smooth ramp 4→10 over 5–15%, then decay 10→6→2→0' },
  { factor: 'RSI(14)',          weight: 10,  detail: 'CC: 38–58=10 · 30–38→4→10 · 58–70→10→0 · <30 or >70=0. Steeper ceiling decay vs CSP — overheated stocks blow through call strikes.',
    definition: 'Relative Strength Index: a momentum oscillator (0–100) measuring the magnitude of recent gains vs. losses over the last 14 trading sessions. Above 70 = overbought; below 30 = oversold.',
    why: 'For CCs: mild weakness (RSI 38–58) favors call sellers — momentum has cooled and the stock is unlikely to surge through your strike. Overbought RSI (>58) is steeper than CSP because momentum stocks easily push through call strikes; oversold RSI (30–38) gets a mean-reversion bonus.',
    formula: 'Wilder-smoothed RSI(14)\n  CC sweet spot 38–58 (lower than CSP 42–62)\n  Steeper ceiling: 58→70 decays 10→0 over 12 RSI pts (vs CSP 13)\n  Mean-reversion: 30–38 ramps 4→10' },
  { factor: 'Chain Median OI', weight: 8,   detail: 'Circuit-breaker · log₁₀(OI)/log₁₀(5000) × 8 · near-always maxed on liquid tickers; bumped from 5 to give small-caps more teeth.',
    definition: 'The median open interest across all call strikes in the 0.10–0.40 delta range. Open interest is the total number of outstanding contracts — a measure of how actively traded the options chain is.',
    why: 'Thin chains mean wide spreads on entry and difficulty rolling if the stock moves against you. Liquid chains = trade near fair value, clean exits, and rolling to a later expiry without hunting for a counterparty.',
    formula: 'Filters candidates to 0.1 < delta < 0.4 first (call chain).\n  pts = min(log10(OI) / log10(5000), 1.0) × 8' },
  { factor: 'DTE Sweet Spot',  weight: 7,   detail: '30–45 DTE = 7 · 21–30 or 45–60 = 4.2 · 14–21 or 60–75 = 2.1 · <14 or >75 = 0.',
    definition: 'A bonus for selecting expirations in the theta-acceleration sweet spot (30–45 days). Theta decay accelerates non-linearly as expiry approaches, peaking in the 30–45 DTE band for premium sellers.',
    why: 'Too short = excessive gamma risk, ATM moves swing P&L violently. Too long = theta crawls, capital tied up unproductively. 30–45 DTE balances rate of decay against gamma exposure.',
    formula: 'Tiered by DTE bucket:\n  30 ≤ DTE ≤ 45 → 7 (sweet spot)\n  21–30 or 45–60 → 4.2 (acceptable)\n  14–21 or 60–75 → 2.1 (suboptimal)\n  else → 0' },
  { factor: 'Earnings in DTE', weight: -15, detail: 'Hard penalty if earnings fall within the expiry window.',
    definition: 'A binary flag — true if the company\'s next earnings announcement date falls within the option\'s expiration window (between today and the expiry date).',
    why: 'Earnings create gap risk in both directions. A post-earnings surge can call your shares away; a collapse damages your underlying. Avoid unless you specifically want to sell a call ahead of earnings.',
    formula: 'earnings_within_dte = True if:\n  0 ≤ (earnings_date − today).days ≤ DTE' },
  { factor: '— STRIKE SCORE (×0.6) —', weight: null, detail: '', definition: '', why: '', formula: '' },
  { factor: 'Delta',            weight: 15,  detail: '+0.20→+0.25=15 · ±1 band=10 · +0.10→+0.15=5 · >+0.30=5.8.',
    definition: 'The rate of change of the option\'s price per $1 move in the stock. For calls, delta ranges from 0 to +1. It approximates the market-implied probability the call expires in-the-money (stock gets called away).',
    why: 'Call delta approximates the probability of expiring in-the-money. +0.20–+0.25 ≈ 20–25% assignment chance — sweet spot for premium vs. keeping shares. Higher delta = more premium but higher chance of losing the position.',
    formula: 'Black-Scholes call delta:\n  d1 = (ln(S/K) + (r + 0.5σ²)T) / (σ√T)\n  call_delta = N(d1)\n  Rescaled from 18 → 15 (×15/18 throughout)' },
  { factor: 'Dist vs Resistance', weight: 18,  detail: 'R within 10% below strike=18 · 10–20% below→3–18 · >20% below=3 · 0–5% above→10 · 5–10% above→0 · >10% above=0 · all R ≤ strike & within 10%=+5.',
    definition: 'The gap between the call strike and the nearest high-volume price level above current price. Volume-profile resistance is a price zone where heavy selling has historically occurred, acting as a natural ceiling on the stock\'s advance.',
    why: 'A resistance level close below your strike acts as an effective ceiling — the stock must break through it to reach you, and sellers typically defend those levels. If resistance is far below (>20%), it sat in the stock\'s old range and is irrelevant to a strike in uncharted territory. All resistance stacked below the strike within 10% earns a +5 multi-layer ceiling bonus.',
    formula: 'Volume Profile resistance (6M / 126-day lookback):\n  nearest_R = min(resistances above current price)\n  gap_pct = (nearest_R − strike) / strike × 100  (negative = R below strike)\n  gap ≤ −20%          → 3 pts  (uncharted territory)\n  −20% < gap ≤ −10%   → 3→18 linear\n  −10% < gap ≤ 0%     → 18 pts  (+5 if all R ≤ strike)\n  0% < gap ≤ 5%       → 18→10\n  5% < gap ≤ 10%      → 10→0\n  gap > 10%           → 0 pts' },
  { factor: 'Exp Move Buffer', weight: 20,  detail: '≥0.2σ above ceiling=20 · 0–0.2σ→13 · −0.1–0σ→5 · deeper inside=0.',
    definition: 'How far above the options-implied 1-standard-deviation expected move the strike sits, measured in units of that expected move. Positive = strike is beyond the statistical ceiling; negative = inside it.',
    why: 'Selling above the 1σ upward expected move gives >68% theoretical probability the stock stays below your strike. Every 0.1σ of additional buffer above the ceiling directly improves the statistical edge at that strike.',
    formula: 'Expected move (1σ upside):\n  EM = S × σ × √T    where T = DTE/365\n  EM_upper = S + EM\n  sigmas_outside = (strike − EM_upper) / EM\n  Positive = strike is above the 1σ ceiling' },
  { factor: '% OTM from Spot', weight: 9,   detail: '≥15%=9 · ≥10%→6.75 · ≥5%→4.5 · ≥2%→1.5 · <2%=0.',
    definition: 'The raw percentage gap between the strike and current stock price. For a call, this is how far the stock must rise before the option goes in-the-money and your shares risk being called away.',
    why: 'Raw distance above current price before assignment risk begins. More room before the stock reaches your strike is a concrete margin of safety independent of IV or time.',
    formula: 'otm_pct = (K − S) / S × 100\n  Raw distance cushion (data-independent, robust to stale IV)\n  Rescaled from 12 → 9 (×0.75 throughout)' },
  { factor: 'Bid-Ask Spread',  weight: 23,  detail: '≤1%=23 · ≤3%→15.3 · ≤5%→8.5 · ≤8%→2.1 · >8%=0.',
    definition: 'The percentage difference between the ask and bid prices relative to the option midpoint: (ask − bid) / mid × 100. Lower means a tighter market and cheaper execution.',
    why: 'Wide spreads directly erode realized premium. A 10% spread on a $1.00 call loses $0.05–$0.10 on entry alone, and you pay it again on every roll. Execution quality determines what you actually collect vs. what the screen shows.',
    formula: 'spread_pct = (ask − bid) / mid × 100\n  Rescaled from 27 → 23 (×23/27 throughout)' },
  { factor: 'OI / Volume',      weight: 5,   detail: 'Circuit-breaker · ≥1000=5 · ≥500→3.5 · ≥200→2 · ≥100→0 · <100=0.',
    definition: 'Open interest (when market closed) or today\'s volume (when market open) at this specific strike — a direct count of active participants.',
    why: 'High OI/volume at this specific strike = efficient price discovery, fast fills near mid, and a liquid exit if the stock surges toward your strike. Low OI = you may be the only participant, making rolling or closing costly.',
    formula: 'Uses volume if US market is open (9:30–16:00 ET weekday)\n  Otherwise uses openInterest at this specific call strike' },
  { factor: 'Annualized ROC',   weight: 10,  detail: '≥30%=10 · 20–30%→7→10 · 12–20%→4→7 · 6–12%→1→4 · <6%=0.',
    definition: 'Annualized return on capital required to hold the underlying shares against a covered call. Measures premium yield against the cash value of the shares, normalized to a one-year timeframe.',
    why: 'Two strikes with identical Δ/EM/spread can have wildly different yields against the cost of holding the underlying. ROC closes that gap and rewards trades that actually pay you meaningfully for the position.',
    formula: 'capital_per_share = current_price − credit\n  ROC = (credit / capital_per_share) × (365 / DTE) × 100\n  CC capital basis = current price (simplification — does not track per-position cost basis)\n  Provisional curve — calibrate against real strikes during validation' },
]

const SCORE_TIERS = [
  { range: '≥ 70', label: 'Strong',   color: '#4ade80', desc: 'All signals aligned and chain is liquid — high-quality, executable CC setup' },
  { range: '45–69', label: 'Moderate', color: '#facc15', desc: 'Most signals ok; some weakness in environment or execution quality' },
  { range: '< 45',  label: 'Weak',     color: '#f87171', desc: 'Poor IV environment, execution risk, earnings overlap, or illiquid chain' },
]

const DECISION_STEPS = [
  { n: 1, q: 'Score ≥ 70?',                                              a: 'Trade it. Steps 2–4 are confirmation, not a gate.' },
  { n: 2, q: 'Am I OK getting called away at this strike?',              a: 'If no, stop. A CC is a conditional sell — only sell the call at a price you’d actually take for the shares.' },
  { n: 3, q: 'What are the 2 biggest factor drags?',                     a: 'Lowest-scoring factors in Env and Strike define the “ticker question” — the specific risk this trade is paying you to accept.' },
  { n: 4, q: 'Can I articulate the thesis that overrides those drags?',  a: 'If no, skip. If yes, size normally and write the thesis down before entering.' },
]

interface Props {
  onScan: (topN: number, minDTE: number, maxDTE: number) => void
  onCustom: (symbols: string[], minDTE: number, maxDTE: number) => void
  loading: boolean
}

export function CcInput({ onScan, onCustom, loading }: Props) {
  const [mode, setMode] = useState<'scan' | 'custom'>('scan')
  const [showLegend, setShowLegend] = useState(false)
  const [expandedFactor, setExpandedFactor] = useState<string | null>(null)

  const [topN, setTopN] = useState(20)
  const [scanMinDTE, setScanMinDTE] = useState(30)
  const [scanMaxDTE, setScanMaxDTE] = useState(60)

  const [chips, setChips] = useState<string[]>([])
  const [inputValue, setInputValue] = useState('')
  const [minDTE, setMinDTE] = useState(30)
  const [maxDTE, setMaxDTE] = useState(60)
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
    else if (minDTE < 1 || maxDTE > 90) err = 'DTE must be between 1 and 90'
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
          title="How the CC score is calculated"
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
          <div className="decision-framework">
            <div className="decision-framework-header">Decision framework — run top-down per row</div>
            <ol className="decision-steps">
              {DECISION_STEPS.map(s => (
                <li key={s.n} className="decision-step">
                  <span className="decision-step-num">{s.n}</span>
                  <span className="decision-step-q">{s.q}</span>
                  <span className="decision-step-a">{s.a}</span>
                </li>
              ))}
            </ol>
          </div>
          <div className="score-legend-factors">
            <div className="score-legend-header">Score breakdown — Final = 0.4 × Env + 0.6 × Strike</div>
            {SCORE_LEGEND.map(f => (
              f.weight === null
                ? <div key={f.factor} className="score-factor-section">{f.factor}</div>
                : <div key={f.factor} className="score-factor-block">
                    <div
                      className="score-factor-row score-factor-row-clickable"
                      onClick={() => setExpandedFactor(expandedFactor === f.factor ? null : f.factor)}
                      title="Click to show calculation"
                    >
                      <span className="score-factor-expand">
                        {expandedFactor === f.factor ? '▾' : '▸'}
                      </span>
                      <span className="score-factor-name">{f.factor}</span>
                      <span
                        className="score-factor-weight"
                        style={{ color: f.weight < 0 ? '#f87171' : f.weight >= 20 ? '#4ade80' : f.weight >= 10 ? '#fbbf24' : '#94a3b8' }}
                      >
                        {f.weight > 0 ? `+${f.weight}` : f.weight} pts
                      </span>
                      <div className="score-factor-bar-wrap">
                        <div className="score-factor-bar" style={{
                          width: f.weight <= 0 ? '0%' : `${Math.min(Math.abs(f.weight) / 30 * 100, 100)}%`,
                          background: f.weight >= 20 ? '#4ade80' : f.weight >= 10 ? '#fbbf24' : '#94a3b8'
                        }} />
                      </div>
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
            <span className="app-subtitle">Ranked by CC composite score — returns top candidates automatically</span>
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
                min={1} max={90} onChange={e => setScanMinDTE(Number(e.target.value))} disabled={loading} />
            </label>
            <label className="filter-item">
              Max DTE
              <input type="number" className="dte-input" value={scanMaxDTE}
                min={1} max={90} onChange={e => setScanMaxDTE(Number(e.target.value))} disabled={loading} />
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
                  min={1} max={90} onChange={e => setMinDTE(Number(e.target.value))} />
              </label>
              <label>
                Max DTE
                <input type="number" className="dte-input" value={maxDTE}
                  min={1} max={90} onChange={e => setMaxDTE(Number(e.target.value))} />
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
