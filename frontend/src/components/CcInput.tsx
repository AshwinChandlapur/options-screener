import { useState, useRef } from 'react'
import type { KeyboardEvent } from 'react'
import { UNIVERSE_OPTIONS, DEFAULT_UNIVERSE, universeSize, type UniverseKey } from '../constants/universes'

const SCORE_LEGEND = [
  { factor: '— ENV SCORE (×0.4) —', weight: null, detail: '', definition: '', why: '', formula: '' },
  { factor: 'IV / HV Ratio',   weight: 35,  detail: '<0.8=0 · 0.8–1.0→5 · 1.0–1.1→5→12.5 · 1.1–1.2→12.5→22.5 · 1.2–1.3→22.5→35 · ≥1.3=35. Stale IV (NaN or ≤0.01) → 0 + flag.',
    definition: 'Implied Volatility divided by 30-day realized (Historical) Volatility. Measures whether options are priced rich or cheap relative to actual recent movement in the stock.',
    why: "IV > HV means the market is pricing in more movement than the stock actually makes — the seller's edge. v3 elevated this to 35 pts because it's now the only volatility-derived factor (HV Rank was dropped as redundant).",
    formula: 'iv_hv_ratio = yfinance_IV / HV_30d\n  v3 rescale: 28 → 35 pts (primary vol signal after HV Rank dropped)\n  Stale-IV trigger: IV is NaN or ≤ 0.01 → IV/HV pts = 0 and row is flagged' },
  { factor: 'Trend (52W)',     weight: 25,  detail: 'CC curve (consolidation): ≤5%=0 · 5–15%→0→25 · 15–25%→25→10 · 25–35%→10→0 · >35%=0.',
    definition: 'Direction-aware trend factor based on distance from the 52-week high. v3 collapsed v2 SMA Alignment (15) and 52W (10) into a single 25-pt direction-aware factor — both were measuring "is this uptrending?".',
    why: 'For CCs: stock at the 52W high has the most upside momentum and the highest risk of being called away. Modest consolidation 5–15% below the high gives the underlying room to drift sideways while premium decays. v3 cliff fix #5: ≤5% near-high now scores 0 (was 4 pts in v2 — that was a participation prize against the assignment-risk thesis).',
    formula: 'dist = (Closeₜ − max(Close, 252d)) / max(Close, 252d) × 100\n  pct_below = abs(min(dist, 0))\n  v3 cliff fix #5: ≤5% bucket dropped from 4 → 0 pts\n  Sweet spot peaks at 15% consolidation; smooth ramps both sides.' },
  { factor: 'RSI(14)',          weight: 20,  detail: 'CC: 38–58=20 · 30–38 linear 0→20 · 58–75 linear 20→0 · <30 or >75=0. Ceiling extended to 75 for AAPL/MSFT-style names in normal trends.',
    definition: 'Relative Strength Index: a momentum oscillator (0–100) measuring the magnitude of recent gains vs. losses over the last 14 trading sessions. Above 70 = overbought; below 30 = oversold.',
    why: 'For CCs: mild weakness (RSI 38–58) favors call sellers — momentum has cooled and the stock is unlikely to surge through your strike. v3 audit fix #8: extended ceiling from 70 to 75 because the v2 knife-edge sent NVDA-style RSI 72 names to 0; now decay is smoother.',
    formula: 'Wilder-smoothed RSI(14)\n  v3 rescale: 10 → 20 pts\n  Audit fix #8: ceiling extended 70 → 75; smooth ramp 58–75' },
  { factor: 'Chain Median OI', weight: 20,  detail: 'Circuit-breaker · log₁₀(OI)/log₁₀(5000) × 20 · saturates near 20 for any liquid name; gives small-caps partial credit on log scale.',
    definition: 'The median open interest across all call strikes in the 0.10–0.40 delta range. Open interest is the total number of outstanding contracts — a measure of how actively traded the options chain is.',
    why: 'Thin chains mean wide spreads on entry and difficulty rolling if the stock moves against you. Liquid chains = trade near fair value, clean exits, and rolling to a later expiry without hunting for a counterparty.',
    formula: 'pts = min(log10(chain_median_oi) / log10(5000), 1.0) × 20\n  v3 rescale: 8 → 20 pts (was a circuit-breaker, now a meaningful liquidity floor)' },
  { factor: 'Earnings in DTE', weight: -15, detail: 'Hard penalty if earnings fall within the expiry window.',
    definition: 'A binary flag — true if the company\'s next earnings announcement date falls within the option\'s expiration window (between today and the expiry date).',
    why: 'Earnings create gap risk in both directions. A post-earnings surge can call your shares away; a collapse damages your underlying. Avoid unless you specifically want to sell a call ahead of earnings.',
    formula: 'earnings_within_dte = True if:\n  0 ≤ (earnings_date − today).days ≤ DTE' },
  { factor: '— STRIKE SCORE (×0.6) —', weight: null, detail: '', definition: '', why: '', formula: '' },
  { factor: 'Delta',            weight: 20,  detail: 'Symmetric bell · |Δ−(+0.225)| ≤ 0.025 = 20 · ≤ 0.075 = 13 · ≤ 0.125 = 7 · outside gate = 0.',
    definition: 'The rate of change of the option\'s price per $1 move in the stock. For calls, delta ranges from 0 to +1. It approximates the market-implied probability the call expires in-the-money (stock gets called away).',
    why: 'Call delta approximates the probability of expiring in-the-money. +0.225 is the sweet spot for premium vs. keeping shares. v3 audit fix #7: aggressive (Δ > +0.30) and conservative (Δ < +0.15) wings now score equally at the same offset from ideal — v2 favored the riskier wing.',
    formula: 'Black-Scholes call delta:\n  d1 = (ln(S/K) + (r + 0.5σ²)T) / (σ√T)\n  call_delta = N(d1)\n  v3: symmetric offset-based bell, ideal = +0.225\n  Gate (+0.10, +0.35) enforced upstream' },
  { factor: 'Bid-Ask Spread',  weight: 30,  detail: '≤1%=30 · 1–3%→30→20 · 3–5%→20→11 · 5–8%→11→3 · >8%=0.',
    definition: 'The percentage difference between the ask and bid prices relative to the option midpoint: (ask − bid) / mid × 100. Lower means a tighter market and cheaper execution.',
    why: 'Wide spreads directly erode realized premium. A 10% spread on a $1.00 call loses $0.05–$0.10 on entry alone, and you pay it again on every roll. v3 weighted execution quality up to 30 pts because it actually differentiates ranking within the gate.',
    formula: 'spread_pct = (ask − bid) / mid × 100\n  v3 rescale: 23 → 30 pts (primary in-gate ranker)' },
  { factor: 'OI / Volume',      weight: 15,  detail: 'Circuit-breaker · ≥1000=15 · 500–1000→10.5→15 · 200–500→6→10.5 · 100–200→0→6 · <100=0.',
    definition: 'Open interest (when market closed) or today\'s volume (when market open) at this specific strike — a direct count of active participants.',
    why: 'High OI/volume at this specific strike = efficient price discovery, fast fills near mid, and a liquid exit if the stock surges toward your strike. Low OI = you may be the only participant, making rolling or closing costly.',
    formula: 'Uses volume if US market is open (9:30–16:00 ET weekday)\n  Otherwise uses openInterest at this specific call strike\n  v3 rescale: 5 → 15 pts' },
  { factor: 'Annualized ROC',   weight: 35,  detail: '≥20%=35 · 14–20%→24.5→35 · 8–14%→14→24.5 · 4–8%→3.5→14 · 2–4%→0→3.5 · <2%=0.',
    definition: 'Annualized return on capital required to hold the underlying shares against a covered call. Measures premium yield against the cash value of the shares, normalized to a one-year timeframe.',
    why: 'ROC is the actual yield — the primary objective for a premium seller. v3 elevated this to 35 pts (the largest weight) because two strikes with identical Δ and spread can have wildly different yields. Cliff fix #6 added a 2–4% ramp.',
    formula: 'capital_per_share = current_price − credit\n  ROC = (credit / capital_per_share) × (365 / DTE) × 100\n  CC capital basis = current price (the underlying held to write the call)\n  v3 rescale: 10 → 35 pts. Cliff fix #6: added 2–4% ramp.' },
  { factor: '— DIAGNOSTIC ONLY (not scored in v3) —', weight: null, detail: '', definition: '', why: '', formula: '' },
  { factor: 'Exp Move Buffer', weight: 0,   detail: 'Computed and shown in the table for visibility. Contributes 0 to score in v3.',
    definition: 'How far above the 0.5× expected move boundary the strike sits, measured in units of the full expected move. Positive = strike is well above the reference ceiling.',
    why: 'Dropped from scoring in v3 (ADR-0007) — the factor was deterministically positive at the configured ideal delta, contributing redundant signal with Δ and %OTM.',
    formula: 'EM = S × σ × √T    where T = DTE/365\n  EM_half_upper = S + 0.5 × EM\n  sigmas_outside = (strike − EM_half_upper) / EM\n  Returned as em_buffer_pct in the response payload but not scored.' },
  { factor: '% OTM from Spot', weight: 0,   detail: 'Computed and shown in the table for visibility. Contributes 0 to score in v3.',
    definition: 'The raw percentage gap between the strike and current stock price.',
    why: 'Dropped from scoring in v3 — deterministic function of Δ and IV; redundant with Δ.',
    formula: 'otm_pct = (K − S) / S × 100\n  Returned in the response payload but not scored.' },
]

const SCORE_TIERS = [
  { range: '≥ 75', label: 'Take it',       color: '#4ade80', desc: 'All signals aligned, rare',                  action: 'Take it, normal size' },
  { range: '65–74', label: 'Take it',       color: '#86efac', desc: 'Solid trade with minor weakness',           action: 'Take it, understand the weakness' },
  { range: '55–64', label: 'Directional',   color: '#facc15', desc: 'Mechanically fine, thesis-dependent',       action: 'Only if you have a directional view' },
  { range: '45–54', label: 'Usually skip',  color: '#fb923c', desc: 'Something structural is off',               action: 'Usually skip' },
  { range: '< 45',  label: 'Skip',          color: '#f87171', desc: 'Multiple red flags',                        action: 'Skip' },
]

const DECISION_STEPS = [
  { n: 1, q: 'Score ≥ 70?',                                              a: 'Trade it. Steps 2–4 are confirmation, not a gate.' },
  { n: 2, q: 'Am I OK getting called away at this strike?',              a: 'If no, stop. A CC is a conditional sell — only sell the call at a price you’d actually take for the shares.' },
  { n: 3, q: 'What are the 2 biggest factor drags?',                     a: 'Lowest-scoring factors in Env and Strike define the “ticker question” — the specific risk this trade is paying you to accept.' },
  { n: 4, q: 'Can I articulate the thesis that overrides those drags?',  a: 'If no, skip. If yes, size normally and write the thesis down before entering.' },
]

interface ExitNode { cond: string; action: string; tone?: 'close' | 'hold' | 'monitor' | 'assign' | 'roll' }
interface ExitBranch { label: string; children: ExitNode[] }
const EXIT_STRATEGY: ExitBranch[] = [
  {
    label: 'Position has ≥ 21 DTE',
    children: [
      { cond: 'Captured ≥ 50% premium',                          action: 'CLOSE',                     tone: 'close' },
      { cond: 'Captured ≥ 25% and > 21 DTE',                      action: 'Consider CLOSE (optional)', tone: 'close' },
      { cond: 'ITM (price > strike)',                              action: 'Monitor — no action yet',   tone: 'monitor' },
      { cond: 'OTM, < 25% captured',                                action: 'HOLD',                      tone: 'hold' },
    ],
  },
  {
    label: 'Position has < 21 DTE',
    children: [
      { cond: 'Captured ≥ 50%',                                    action: 'CLOSE',                     tone: 'close' },
      { cond: 'OTM (price < strike)',                                 action: 'Let it expire worthless — keep full premium + shares', tone: 'hold' },
      { cond: 'ITM, strike ≥ cost basis, happy to sell here',       action: 'Let assign',                tone: 'assign' },
      { cond: 'ITM, thesis broken or strike below cost basis',      action: 'ROLL up/out for credit, else accept the called-away loss', tone: 'roll' },
    ],
  },
]

interface Props {
  onScan: (topN: number, minDTE: number, maxDTE: number, universe: UniverseKey) => void
  onCustom: (symbols: string[], minDTE: number, maxDTE: number) => void
  loading: boolean
}

export function CcInput({ onScan, onCustom, loading }: Props) {
  const [mode, setMode] = useState<'scan' | 'custom'>('custom')
  const [showLegend, setShowLegend] = useState(false)
  const [expandedFactor, setExpandedFactor] = useState<string | null>(null)

  const [topN, setTopN] = useState(20)
  const [scanMinDTE, setScanMinDTE] = useState(30)
  const [scanMaxDTE, setScanMaxDTE] = useState(60)
  const [universe, setUniverse] = useState<UniverseKey>(DEFAULT_UNIVERSE)

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
    onScan(topN, scanMinDTE, scanMaxDTE, universe)
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
            <div className="score-tier-table-header">
              <span>Score</span>
              <span>Interpretation</span>
              <span>Action</span>
            </div>
            {SCORE_TIERS.map(t => (
              <div key={t.range} className="score-tier">
                <span className="score-tier-range" style={{ color: t.color, fontWeight: 700 }}>{t.range}</span>
                <span className="score-tier-desc">{t.desc}</span>
                <span className="score-tier-action">{t.action}</span>
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
          <div className="exit-strategy">
            <div className="decision-framework-header">Exit strategy — manage after fill</div>
            {EXIT_STRATEGY.map(branch => (
              <div key={branch.label} className="exit-branch">
                <div className="exit-branch-label">{branch.label}</div>
                <ul className="exit-children">
                  {branch.children.map(n => (
                    <li key={n.cond} className="exit-child">
                      <span className="exit-cond">{n.cond}</span>
                      <span className="exit-arrow">→</span>
                      <span className={`exit-action exit-action-${n.tone ?? 'hold'}`}>{n.action}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
            <div className="thumb-rule">
              <span className="thumb-rule-label">Thumb rule</span>
              <span className="thumb-rule-text">
                At 21 DTE: <em>is remaining premium worth the gamma risk?</em>
                &nbsp;Close if near-the-money or you don’t want to be called away. Run it only if deep OTM with thin extrinsic, or strike ≥ basis and you’re happy to sell here.
              </span>
            </div>
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
            <div style={{ marginTop: '6px', padding: '5px 8px', background: '#0f172a', borderRadius: '5px', fontSize: '11px', color: '#64748b', borderLeft: '3px solid #334155' }}>
              <strong style={{ color: '#94a3b8' }}>Tie-break:</strong> equal scores → higher <strong>Ann. ROC</strong> wins.
            </div>
          </div>
        </div>
      )}

      {mode === 'scan' && (
        <div className="momentum-scan-row">
          <div className="momentum-scan-info">
            <span className="scan-desc">
              Scans <strong>{universeSize(universe)}</strong> stocks — {UNIVERSE_OPTIONS.find(o => o.key === universe)?.hint}
            </span>
            <span className="app-subtitle">Ranked by CC composite score — returns top candidates automatically</span>
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
