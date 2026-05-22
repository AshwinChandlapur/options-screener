/**
 * NarrativeIcMonitor — Live 90-day IC test dashboard.
 *
 * Displays:
 *   1. Summary header (cumulative IC, sample size, p-value, progress bar)
 *   2. Weekly IC table (IC by cohort week)
 *   3. Interpretation guide.
 *
 * FROZEN: do not add UI filters or controls that could alter the test
 * interpretation.  This panel is read-only for 90 days from first snapshot.
 */

import type { IcMonitor, WeeklyIcPoint, FactorIcEntry, AsymmetryBucket } from '../types/narrative'
import { useIcMonitor } from '../hooks/useIcMonitor'

function fmt(v: number | null, digits = 3): string {
  if (v === null) return '—'
  return v.toFixed(digits)
}

function icValueClass(ic: number | null): string {
  if (ic === null) return ''
  if (ic >= 0.08) return 'ic-good'
  if (ic >= 0.04) return 'ic-ok'
  if (ic >= 0) return 'ic-poor'
  return 'ic-bad'
}

function StatusBadge({ ic, n }: { ic: number | null; n: number }) {
  if (n < 10) return <span className="ic-status-badge ic-status-accumulating">Accumulating data</span>
  if (ic === null) return <span className="ic-status-badge ic-status-accumulating">—</span>
  if (ic >= 0.08) return <span className="ic-status-badge ic-status-signal">Signal candidate (IC ≥ 0.08)</span>
  if (ic >= 0.04) return <span className="ic-status-badge ic-status-below">Below threshold</span>
  return <span className="ic-status-badge ic-status-none">No signal</span>
}

function ProgressBar({ pct }: { pct: number }) {
  return (
    <div className="ic-progress-track">
      <div className="ic-progress-fill" style={{ width: `${Math.min(Math.round(pct * 100), 100)}%` }} />
    </div>
  )
}

function WeekRow({ row }: { row: WeeklyIcPoint }) {
  const retColor = row.mean_return_pct >= 0 ? '#4ade80' : '#f87171'
  return (
    <tr>
      <td style={{ fontFamily: 'monospace', padding: '8px 12px' }}>{row.week_label}</td>
      <td style={{ textAlign: 'center', padding: '8px 12px', color: '#94a3b8' }}>{row.n_pairs}</td>
      <td style={{ textAlign: 'center', padding: '8px 12px', fontFamily: 'monospace', fontWeight: 600 }}
          className={icValueClass(row.ic)}>
        {fmt(row.ic)}
      </td>
      <td style={{ textAlign: 'center', padding: '8px 12px', color: '#94a3b8' }}>{fmt(row.p_value)}</td>
      <td style={{ textAlign: 'center', padding: '8px 12px', color: '#94a3b8' }}>{row.mean_acs.toFixed(1)}</td>
      <td style={{ textAlign: 'center', padding: '8px 12px', color: retColor }}>
        {row.mean_return_pct >= 0 ? '+' : ''}{row.mean_return_pct.toFixed(2)}%
      </td>
    </tr>
  )
}

const _FACTOR_LABELS: Record<string, string> = {
  acs:               'ACS (total)',
  acs_raw:           'ACS raw (pre-haircut)',
  decay_acs:         'ACS (time-decayed)',
  comp_a:            'A — Attention persistence',
  comp_b:            'B — Contributor quality',
  comp_c:            'C — Narrative strength',
  comp_d:            'D — Thesis quality',
  dwd_14d:           'Decay-weighted density (A input)',
  gini_14d:          'Gini coefficient (B input)',
  stage_confidence:  'Stage confidence (C input)',
  s_br:              'Bull-researched share (D input)',
  s_Br:              'Bear-researched share (D input)',
  acs_slope_14d:     'ACS slope 14d (momentum)',
  stage_streak_days: 'Stage streak days (continuity)',
}

function FactorRow({ row }: { row: FactorIcEntry }) {
  const sig = row.p_value !== null && row.p_value < 0.05
  return (
    <tr>
      <td style={{ padding: '7px 12px', color: '#cbd5e1' }}>
        {_FACTOR_LABELS[row.factor] ?? row.factor}
      </td>
      <td style={{ textAlign: 'center', padding: '7px 12px', color: '#64748b' }}>{row.n}</td>
      <td style={{ textAlign: 'center', padding: '7px 12px', fontFamily: 'monospace', fontWeight: 600 }}
          className={icValueClass(row.ic)}>
        {fmt(row.ic)}
      </td>
      <td style={{ textAlign: 'center', padding: '7px 12px', color: sig ? '#a3e635' : '#64748b' }}>
        {row.p_value !== null ? fmt(row.p_value) : '—'}
      </td>
    </tr>
  )
}

function tailRatioColor(r: number | null): string {
  if (r === null) return '#64748b'
  if (r >= 2.0) return '#4ade80'
  if (r >= 1.2) return '#86efac'
  if (r >= 0.8) return '#94a3b8'
  return '#f87171'
}

function AsymmetryRow({ row }: { row: AsymmetryBucket }) {
  const hasData = row.mean_ret !== null
  const pct = (v: number | null) => v === null ? '—' : `${(v * 100).toFixed(1)}%`
  const num = (v: number | null, d = 1) => v === null ? '—' : v.toFixed(d)
  const isHighlight = row.label.includes('top quartile') || row.label.includes('Emerging')
  return (
    <tr style={isHighlight ? { background: 'rgba(99,102,241,0.06)' } : undefined}>
      <td style={{ padding: '7px 12px', color: isHighlight ? '#a5b4fc' : '#cbd5e1', fontWeight: isHighlight ? 600 : 400 }}>
        {row.label}
      </td>
      <td style={{ textAlign: 'center', padding: '7px 12px', color: '#64748b' }}>{row.n}</td>
      {!hasData ? (
        <td colSpan={7} style={{ textAlign: 'center', padding: '7px 12px', color: '#475569', fontStyle: 'italic' }}>
          Need {20} pairs
        </td>
      ) : (
        <>
          <td style={{ textAlign: 'right', padding: '7px 12px', color: (row.mean_ret ?? 0) >= 0 ? '#4ade80' : '#f87171', fontFamily: 'monospace' }}>
            {(row.mean_ret ?? 0) >= 0 ? '+' : ''}{num(row.mean_ret, 2)}%
          </td>
          <td style={{ textAlign: 'right', padding: '7px 12px', color: '#94a3b8', fontFamily: 'monospace' }}>
            {(row.median_ret ?? 0) >= 0 ? '+' : ''}{num(row.median_ret, 2)}%
          </td>
          <td style={{ textAlign: 'right', padding: '7px 12px', color: '#94a3b8' }}>{num(row.skewness, 2)}</td>
          <td style={{ textAlign: 'right', padding: '7px 12px', color: '#94a3b8' }}>{pct(row.win_rate)}</td>
          <td style={{ textAlign: 'right', padding: '7px 12px', color: '#4ade80' }}>{pct(row.upside_10)}</td>
          <td style={{ textAlign: 'right', padding: '7px 12px', color: '#f87171' }}>{pct(row.downside_10)}</td>
          <td style={{ textAlign: 'right', padding: '7px 12px', fontFamily: 'monospace', fontWeight: 600, color: tailRatioColor(row.tail_ratio) }}>
            {row.tail_ratio !== null ? `${row.tail_ratio.toFixed(2)}×` : '—'}
          </td>
        </>
      )}
    </tr>
  )
}

function IcSummary({ data }: { data: IcMonitor }) {
  const daysElapsed = data.window_start
    ? Math.floor((Date.now() - new Date(data.window_start).getTime()) / 86_400_000)
    : 0
  const daysRemaining = Math.max(0, 90 - daysElapsed)
  const pValueGood = data.cumulative_p_value !== null && data.cumulative_p_value < 0.05

  return (
    <div className="ic-stat-grid">
      <div className="ic-stat-card">
        <div className="ic-stat-label">Cumulative IC</div>
        <div className={`ic-stat-value ${icValueClass(data.cumulative_ic)}`}>
          {fmt(data.cumulative_ic)}
        </div>
        <StatusBadge ic={data.cumulative_ic} n={data.cumulative_n} />
      </div>

      <div className="ic-stat-card">
        <div className="ic-stat-label">Complete pairs</div>
        <div className="ic-stat-value">
          {data.cumulative_n}
          <span style={{ fontSize: 14, fontWeight: 400, color: '#64748b' }}> / {data.total_snapshots}</span>
        </div>
        <ProgressBar pct={data.pct_complete} />
        <div className="ic-stat-sub">{Math.round(data.pct_complete * 100)}% returns filled</div>
      </div>

      <div className="ic-stat-card">
        <div className="ic-stat-label">p-value</div>
        <div className={`ic-stat-value ${pValueGood ? 'ic-good' : ''}`}>
          {fmt(data.cumulative_p_value)}
        </div>
        <div className="ic-stat-sub">Target: p &lt; 0.05</div>
      </div>

      <div className="ic-stat-card">
        <div className="ic-stat-label">Window progress</div>
        <div className="ic-stat-value">
          {daysElapsed}
          <span style={{ fontSize: 14, fontWeight: 400, color: '#64748b' }}> / 90 days</span>
        </div>
        <ProgressBar pct={Math.min(daysElapsed / 90, 1)} />
        <div className="ic-stat-sub">{daysRemaining} days remaining</div>
      </div>
    </div>
  )
}

function ExperimentGuide() {
  return (
    <details className="ic-guide">
      <summary className="ic-guide-summary">Monitoring guide &amp; end-of-experiment checklist</summary>

      <div className="ic-guide-body">

        <section className="ic-guide-section">
          <h5>Every day — infrastructure health</h5>
          <ul>
            <li><strong>Scorer job</strong> — Azure Portal → Container Apps → <code>job-acs-scorer</code> → Execution history. All runs should show <em>Succeeded</em>. Any <em>Failed</em> run means a snapshot day was skipped and that date is a gap in the IC series.</li>
            <li><strong>Snapshot count growing</strong> — run <code>scripts/_check_ic.py</code> or query Cosmos: <code>SELECT VALUE COUNT(1) FROM c WHERE c.is_complete = false</code> on <code>ic_snapshots</code>. Expect +N new docs per run (N = tracked ticker count, ~60–80).</li>
            <li><strong>Backfill job</strong> — <code>job-narrative-backfill</code> fills <code>signal_events</code> px_t5/t10/t20 nightly. Check the Signals tab here: the fill stage column should progress from "queued → T+0 → T+5 → T+10 → complete" over time.</li>
          </ul>
        </section>

        <section className="ic-guide-section">
          <h5>Weekly — IC Monitor checks (after day 30)</h5>
          <ul>
            <li><strong>First complete pairs appear</strong> ~30 days after the first snapshot (≈ June 21). The weekly IC table populates once ≥ 10 pairs exist in a cohort.</li>
            <li><strong>IC trend</strong> — scan the weekly IC table. IC should be positive and ideally converging. A consistently negative IC means ACS is an inverse predictor (unusual but informative).</li>
            <li><strong>Per-factor table</strong> — check which components (A, B, C, D) individually predict returns. If only D (thesis quality) has IC &gt; 0 you know A+B saturation is confirmed.</li>
            <li><strong>Small/mid-cap asymmetry</strong> — in the Return Asymmetry table, "Small/mid-cap (&lt;$10B)" tail ratio should exceed "Large cap (≥$10B)". That's the core thesis: ACS is a better predictor for less-covered names.</li>
          </ul>
        </section>

        <section className="ic-guide-section">
          <h5>Signal Performance tab — what to track</h5>
          <ul>
            <li><strong>Hit rate at T+5 / T+10 / T+20</strong> — stat cards at top. Target: hit rate &gt; 55% (directional edge). Below 45% = no edge.</li>
            <li><strong>Median excess return vs SPY</strong> — shown under each stat card. Positive = alpha over market.</li>
            <li><strong>Filter to 2→3 transition</strong> — the entry-window signal. This is the cleanest signal type: ticker enters stage 3 (thesis fully formed, peak score). Compare its hit rate vs baseline ("Any" transition).</li>
            <li><strong>Raw events table</strong> — shows each individual stage transition with fill status. A row stuck at "queued" means the backfill job hasn't fetched T+0 price yet.</li>
          </ul>
        </section>

        <section className="ic-guide-section">
          <h5>End-of-experiment verdict (day 90, ≈ August 19)</h5>
          <table className="ic-guide-table">
            <thead>
              <tr><th>Outcome</th><th>Criteria</th><th>Action</th></tr>
            </thead>
            <tbody>
              <tr className="ic-guide-signal">
                <td>✓ Signal confirmed</td>
                <td>Cumulative IC ≥ 0.08, p &lt; 0.05, tail ratio &gt; 1.2 for small/mid-cap segment</td>
                <td>ACS is a valid predictor. Proceed to calibrate position sizing. Consider tightening the universe to &lt;$10B names.</td>
              </tr>
              <tr className="ic-guide-weak">
                <td>~ Weak signal</td>
                <td>IC 0.04–0.08 or p &gt; 0.05 but positive</td>
                <td>Directional tendency but not tradeable standalone. Investigate which factors drive it (per-factor table). Run a second 90-day window with a revised formula.</td>
              </tr>
              <tr className="ic-guide-none">
                <td>✗ No signal</td>
                <td>IC &lt; 0.04 or negative</td>
                <td>ACS does not predict 30d returns. Review: (a) Component A recalibration to relative growth vs baseline, (b) shorter horizon (10d IC), (c) conditioning on market regime before re-running.</td>
              </tr>
            </tbody>
          </table>
          <p style={{ fontSize: 12, color: '#64748b', marginTop: 8 }}>
            All results are visible here in this tab (IC Monitor) and in the Signals tab below. No external tool needed to read the verdict.
          </p>
        </section>

      </div>
    </details>
  )
}


export function NarrativeIcMonitor() {
  const { data, loading, error, lastUpdatedAt } = useIcMonitor()

  return (
    <div className="ic-monitor">
      <div className="narrative-header" style={{ marginBottom: 0 }}>
        <div>
          <h2>Live IC Test</h2>
          <p className="narrative-subtitle">
            90-day validation window — Spearman IC between ACS and 30-day forward return.
            {' '}Threshold: IC ≥ 0.08 at p &lt; 0.05. Frozen; no changes during window.
          </p>
        </div>
      </div>

      {loading && !data && (
        <p className="muted" style={{ fontSize: 13 }}>Loading IC monitor…</p>
      )}

      {error && (
        <div className="info-banner">
          {error.unavailable
            ? 'IC monitor not yet available — snapshots begin accumulating on first scorer run after deployment.'
            : error.detail}
        </div>
      )}

      {data && (
        <>
          <IcSummary data={data} />

          <div className="ic-section">
            {data.weekly.length === 0 ? (
              <div className="ic-no-data">
                No complete (ACS, return) pairs yet. First returns available
                {data.window_start
                  ? ` after ${new Date(
                      new Date(data.window_start).getTime() + 30 * 86_400_000
                    ).toLocaleDateString()}.`
                  : ' 30 days after the first snapshot.'}
              </div>
            ) : (
              <div className="table-wrapper">
                <table className="screener-table">
                  <thead>
                    <tr>
                      <th>ISO Week</th>
                      <th style={{ textAlign: 'center' }}>Pairs</th>
                      <th style={{ textAlign: 'center' }}>IC (Spearman ρ)</th>
                      <th style={{ textAlign: 'center' }}>p-value</th>
                      <th style={{ textAlign: 'center' }}>Mean ACS</th>
                      <th style={{ textAlign: 'center' }}>Mean Ret (30d)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.weekly.map(row => <WeekRow key={row.week_label} row={row} />)}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="ic-legend">
            <strong>Interpretation guide</strong><br />
            <span className="ic-good">IC ≥ 0.08 + p &lt; 0.05</span> = minimum credible signal (institutional threshold).<br />
            <span className="ic-ok">IC 0.04–0.08</span> = below threshold — directional tendency, not yet tradeable.<br />
            <span className="ic-bad">IC &lt; 0.04 or negative</span> = no signal detected — formula revision needed.<br />
            Window: {data.window_start ?? '—'} → {data.window_end ?? '—'}
            {lastUpdatedAt && <span className="muted"> · Refreshed {lastUpdatedAt.toLocaleTimeString()}</span>}
          </div>

          {data.factor_ics && data.factor_ics.length > 0 && (
            <div className="ic-section">
              <h4 style={{ color: '#94a3b8', fontSize: 13, fontWeight: 600, marginBottom: 10, letterSpacing: '0.05em', textTransform: 'uppercase' }}>
                Per-factor IC (all complete pairs)
              </h4>
              {data.factor_ics.every(f => f.ic === null) ? (
                <div className="ic-no-data">Awaiting minimum {10} complete pairs per factor.</div>
              ) : (
                <div className="table-wrapper">
                  <table className="screener-table">
                    <thead>
                      <tr>
                        <th>Factor</th>
                        <th style={{ textAlign: 'center' }}>n</th>
                        <th style={{ textAlign: 'center' }}>IC (Spearman ρ)</th>
                        <th style={{ textAlign: 'center' }}>p-value</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.factor_ics.map(f => <FactorRow key={f.factor} row={f} />)}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {data.asymmetry && data.asymmetry.length > 0 && (
            <div className="ic-section">
              <h4 style={{ color: '#94a3b8', fontSize: 13, fontWeight: 600, marginBottom: 4, letterSpacing: '0.05em', textTransform: 'uppercase' }}>
                Return Asymmetry by Segment
              </h4>
              <p style={{ color: '#64748b', fontSize: 12, marginBottom: 10 }}>
                Do high-ACS / Emerging tickers show right-skewed returns? Tail ratio = (% &gt;+10%) ÷ (% &lt;−10%). &gt;1 = more right tail than left.
              </p>
              <div className="table-wrapper">
                <table className="screener-table">
                  <thead>
                    <tr>
                      <th>Segment</th>
                      <th style={{ textAlign: 'center' }}>n</th>
                      <th style={{ textAlign: 'right' }}>Mean ret</th>
                      <th style={{ textAlign: 'right' }}>Median ret</th>
                      <th style={{ textAlign: 'right' }}>Skew</th>
                      <th style={{ textAlign: 'right' }}>Win rate</th>
                      <th style={{ textAlign: 'right' }} title="% of returns > +10%">&gt;+10%</th>
                      <th style={{ textAlign: 'right' }} title="% of returns < -10%">&lt;−10%</th>
                      <th style={{ textAlign: 'right' }} title="upside tail / downside tail ratio">Tail ratio</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.asymmetry.map(b => <AsymmetryRow key={b.label} row={b} />)}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          <ExperimentGuide />
        </>
      )}

      {!data && !loading && <ExperimentGuide />}
    </div>
  )
}


