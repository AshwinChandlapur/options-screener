import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
  createColumnHelper,
  type SortingState,
} from '@tanstack/react-table'
import { Fragment, useState } from 'react'
import type { SwingResult } from '../types/swing'

const col = createColumnHelper<SwingResult>()

function fmt2(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(2)
}
function fmt1(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(1)
}
function fmtPct(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(1) + '%'
}

const SETUP_COLOR: Record<string, string> = {
  breakout: '#60a5fa',
  momentum: '#4ade80',
  reversion: '#fbbf24',
  retest: '#a78bfa',
}

const CONFIDENCE_BADGE: Record<string, { color: string; bg: string; label: string }> = {
  high: { color: '#022c22', bg: '#4ade80', label: 'HIGH' },
  medium: { color: '#1e1b00', bg: '#fbbf24', label: 'MED' },
  speculative: { color: '#1e1b22', bg: '#a78bfa', label: 'SPEC' },
}

function scoreColor(s: number): string {
  if (s >= 75) return '#4ade80'
  if (s >= 65) return '#86efac'
  if (s >= 55) return '#fbbf24'
  if (s >= 45) return '#fb923c'
  return '#f87171'
}

interface Props {
  data: SwingResult[]
}

export function SwingTable({ data }: Props) {
  const [sorting, setSorting] = useState<SortingState>([{ id: 'swing_score', desc: true }])
  const [expandedRow, setExpandedRow] = useState<string | null>(null)

  const columns = [
    col.accessor('symbol', {
      header: 'Symbol',
      cell: info => (
        <span style={{ fontWeight: 600 }}>
          {info.getValue()}
          {info.row.original.earnings_warning && (
            <span title="Earnings within 10 days" style={{ marginLeft: 4, color: '#fbbf24' }}>⚠</span>
          )}
        </span>
      ),
    }),
    col.accessor('price', { header: 'Price', cell: i => `$${fmt2(i.getValue())}` }),
    col.accessor('setup_type', {
      header: 'Setup',
      cell: info => {
        const v = info.getValue()
        const color = SETUP_COLOR[v] ?? '#94a3b8'
        return (
          <span style={{ color, fontWeight: 500, textTransform: 'capitalize' }}>
            {v || '—'}
          </span>
        )
      },
    }),
    col.accessor('swing_score', {
      header: 'Score',
      cell: info => {
        const v = info.getValue()
        return (
          <span style={{ color: scoreColor(v), fontWeight: 700 }}>
            {v.toFixed(1)}
          </span>
        )
      },
    }),
    col.accessor('confidence', {
      header: 'Confidence',
      cell: info => {
        const c = CONFIDENCE_BADGE[info.getValue()]
        if (!c) return <span>—</span>
        return (
          <span style={{
            background: c.bg,
            color: c.color,
            padding: '2px 8px',
            borderRadius: 4,
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: 0.5,
          }}>
            {c.label}
          </span>
        )
      },
    }),
    col.accessor('rr', {
      header: 'R:R',
      cell: info => {
        const v = info.getValue()
        const color = v >= 3.5 ? '#4ade80' : v >= 2.75 ? '#86efac' : v >= 2.5 ? '#fbbf24' : '#f87171'
        return <span style={{ color, fontWeight: 600 }}>{fmt1(v)}</span>
      },
    }),
    col.accessor('entry', {
      header: 'Entry (trigger)',
      cell: info => {
        const row = info.row.original
        const trig = row.trigger_kind
        const kindLabel: Record<string, string> = {
          break_above: 'break ↑',
          pullback_to_ema8: 'pull → EMA8',
          reclaim_confirm: 'confirm',
          retest_of: 'retest',
          market_close: 'at close',
        }
        return (
          <span>
            <span style={{ fontWeight: 600 }}>${fmt2(info.getValue())}</span>
            {trig && trig in kindLabel && (
              <span style={{
                marginLeft: 6,
                fontSize: 9,
                color: '#94a3b8',
                background: '#1e2235',
                padding: '1px 5px',
                borderRadius: 3,
                letterSpacing: 0.3,
              }}>
                {kindLabel[trig]}
              </span>
            )}
            {row.extended && (
              <span title="Current price is more than 3% past the trigger — chasing entry"
                style={{
                  marginLeft: 4,
                  fontSize: 9,
                  color: '#fbbf24',
                  background: '#3a2e0a',
                  padding: '1px 5px',
                  borderRadius: 3,
                  fontWeight: 700,
                  letterSpacing: 0.3,
                }}>
                CHASING
              </span>
            )}
          </span>
        )
      },
    }),
    col.accessor('stop', { header: 'Stop', cell: i => (
      <span style={{ color: '#f87171' }}>${fmt2(i.getValue())}</span>
    ) }),
    col.accessor('target', { header: 'Target', cell: i => (
      <span style={{ color: '#4ade80' }}>${fmt2(i.getValue())}</span>
    ) }),
    col.accessor(row => `${row.hold_min_days}–${row.hold_max_days}d`, {
      id: 'hold',
      header: 'Hold',
      cell: info => <span style={{ fontSize: 11 }}>{info.getValue()}</span>,
    }),
    col.accessor('setup_score', {
      header: 'Setup pts',
      cell: i => <span style={{ fontSize: 11 }}>{i.getValue().toFixed(0)}/100</span>,
    }),
  ]

  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })

  if (data.length === 0) return null

  return (
    <div className="table-wrapper">
      <table className="screener-table">
        <thead>
          {table.getHeaderGroups().map(hg => (
            <tr key={hg.id}>
              {hg.headers.map(h => (
                <th
                  key={h.id}
                  className="sortable"
                  onClick={h.column.getToggleSortingHandler()}
                >
                  {flexRender(h.column.columnDef.header, h.getContext())}
                  {{ asc: ' ▲', desc: ' ▼' }[h.column.getIsSorted() as string] ?? ''}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map(row => {
            const r = row.original
            const isExpanded = expandedRow === r.symbol
            return (
              <Fragment key={r.symbol}>
                <tr
                  onClick={() => setExpandedRow(isExpanded ? null : r.symbol)}
                  style={{ cursor: 'pointer' }}
                >
                  {row.getVisibleCells().map(c => (
                    <td key={c.id}>{flexRender(c.column.columnDef.cell, c.getContext())}</td>
                  ))}
                </tr>
                {isExpanded && (
                  <tr className="sub-exp-row">
                    <td colSpan={columns.length}>
                      {r.extended && (
                        <div style={{
                          margin: '0 12px 8px',
                          padding: '6px 10px',
                          background: '#3a2e0a',
                          border: '1px solid #fbbf24',
                          borderRadius: 4,
                          fontSize: 12,
                          color: '#fbbf24',
                        }}>
                          ⚠ <strong>Chasing</strong> — current price (${fmt2(r.price)}) is more than 3% past
                          the structural trigger (${fmt2(r.entry)}). Wait for a pullback or skip; entering here
                          degrades the real R:R to roughly {(((r.target - r.price) / Math.max(0.01, r.price - r.stop))).toFixed(1)}.
                        </div>
                      )}
                      <div style={{
                        margin: '0 12px 8px',
                        padding: '6px 10px',
                        background: '#0f172a',
                        borderRadius: 4,
                        fontSize: 12,
                        display: 'grid',
                        gridTemplateColumns: 'repeat(5, 1fr)',
                        gap: 8,
                      }}>
                        <div><span style={{ color: '#64748b' }}>Trigger</span><br/><strong>${fmt2(r.entry)}</strong></div>
                        <div><span style={{ color: '#64748b' }}>Current</span><br/><strong>${fmt2(r.price)}</strong></div>
                        <div><span style={{ color: '#64748b' }}>Stop</span><br/><strong style={{ color: '#f87171' }}>${fmt2(r.stop)}</strong></div>
                        <div><span style={{ color: '#64748b' }}>Target</span><br/><strong style={{ color: '#4ade80' }}>${fmt2(r.target)}</strong></div>
                        <div><span style={{ color: '#64748b' }}>R:R</span><br/><strong>{fmt1(r.rr)}</strong> ({r.trigger_kind.replace(/_/g, ' ')})</div>
                      </div>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, padding: 12 }}>
                        <div>
                          <h4 style={{ margin: '0 0 8px', fontSize: 13 }}>Setup Drivers</h4>
                          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
                            {r.drivers.map((d, i) => <li key={i}>{d}</li>)}
                          </ul>
                          {r.narrative && (
                            <>
                              <h4 style={{ margin: '12px 0 4px', fontSize: 13 }}>AI Narrative</h4>
                              <p style={{ margin: 0, fontSize: 12, lineHeight: 1.5 }}>{r.narrative}</p>
                            </>
                          )}
                          {r.risk_note && (
                            <>
                              <h4 style={{ margin: '8px 0 4px', fontSize: 13, color: '#fbbf24' }}>Invalidation</h4>
                              <p style={{ margin: 0, fontSize: 12, lineHeight: 1.5, color: '#fbbf24' }}>{r.risk_note}</p>
                            </>
                          )}
                        </div>
                        <div>
                          <h4 style={{ margin: '0 0 8px', fontSize: 13 }}>Score Breakdown</h4>
                          <table style={{ fontSize: 11, width: '100%' }}>
                            <tbody>
                              <tr><td>R:R</td><td style={{ textAlign: 'right' }}>{r.breakdown.rr?.toFixed(1)} / 40</td></tr>
                              <tr><td>Setup</td><td style={{ textAlign: 'right' }}>{r.breakdown.setup?.toFixed(1)} / 30</td></tr>
                              <tr><td>Context (ADX + A/D)</td><td style={{ textAlign: 'right' }}>{r.breakdown.context?.toFixed(1)} / 20</td></tr>
                              <tr><td>Institutional</td><td style={{ textAlign: 'right' }}>{r.breakdown.institutional?.toFixed(1)} / 10</td></tr>
                              <tr style={{ borderTop: '1px solid #334155' }}>
                                <td style={{ paddingTop: 4 }}>Raw subtotal</td>
                                <td style={{ textAlign: 'right', paddingTop: 4 }}>{r.raw_score.toFixed(1)} / 100</td>
                              </tr>
                            </tbody>
                          </table>
                          {r.multipliers && Object.keys(r.multipliers).length > 0 && (
                            <>
                              <h4 style={{ margin: '12px 0 4px', fontSize: 13 }}>Multipliers</h4>
                              <table style={{ fontSize: 11, width: '100%' }}>
                                <tbody>
                                  <tr>
                                    <td>Regime ({r.regime_label || 'baseline'})</td>
                                    <td style={{ textAlign: 'right', color: r.multipliers.regime < 1 ? '#fbbf24' : '#94a3b8' }}>
                                      ×{r.multipliers.regime?.toFixed(2)}
                                    </td>
                                  </tr>
                                  <tr>
                                    <td>
                                      Earnings
                                      {r.days_to_earnings != null && ` (${r.days_to_earnings}d)`}
                                    </td>
                                    <td style={{ textAlign: 'right', color: r.multipliers.earnings < 1 ? '#fbbf24' : '#94a3b8' }}>
                                      ×{r.multipliers.earnings?.toFixed(2)}
                                    </td>
                                  </tr>
                                  <tr>
                                    <td>Extended {r.extended ? '(chasing)' : ''}</td>
                                    <td style={{ textAlign: 'right', color: r.multipliers.extended < 1 ? '#fbbf24' : '#94a3b8' }}>
                                      ×{r.multipliers.extended?.toFixed(2)}
                                    </td>
                                  </tr>
                                  <tr style={{ borderTop: '1px solid #334155' }}>
                                    <td style={{ paddingTop: 4, fontWeight: 600 }}>Final score</td>
                                    <td style={{ textAlign: 'right', paddingTop: 4, fontWeight: 700 }}>
                                      {r.swing_score.toFixed(1)} / 100
                                    </td>
                                  </tr>
                                  {r.rr_gate > 0 && (
                                    <tr>
                                      <td style={{ color: '#64748b' }}>R:R gate</td>
                                      <td style={{ textAlign: 'right', color: '#64748b' }}>≥ {r.rr_gate.toFixed(1)}</td>
                                    </tr>
                                  )}
                                  {r.forced_short_hold && (
                                    <tr>
                                      <td colSpan={2} style={{ color: '#fbbf24', fontSize: 10, paddingTop: 4 }}>
                                        ⚠ Hold window trimmed to avoid earnings
                                      </td>
                                    </tr>
                                  )}
                                </tbody>
                              </table>
                            </>
                          )}
                          <h4 style={{ margin: '12px 0 4px', fontSize: 13 }}>Setup Scores</h4>
                          <table style={{ fontSize: 11, width: '100%' }}>
                            <tbody>
                              {Object.entries(r.setup_scores).map(([k, v]) => (
                                <tr key={k}>
                                  <td style={{ textTransform: 'capitalize' }}>{k}</td>
                                  <td style={{ textAlign: 'right', fontWeight: k === r.setup_type ? 600 : 400 }}>
                                    {v.toFixed(0)}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                          <h4 style={{ margin: '12px 0 4px', fontSize: 13 }}>Signals</h4>
                          <div style={{ fontSize: 11, lineHeight: 1.6 }}>
                            <div>RSI: {fmt1(r.rsi)} · ADX: {fmt1(r.adx)} · ATR: {fmt2(r.atr14)}</div>
                            <div>RS vs SPY: {fmt2(r.rs_vs_spy)} · EMA align: {r.ema_alignment_score ?? '—'}/9</div>
                            <div>A/D slope: {fmtPct(r.ad_line_slope_pct)} · Inst own: {fmtPct(r.institutional_ownership_pct)}</div>
                            {r.consolidation_days && r.consolidation_days > 0 && (
                              <div>Base: {r.consolidation_days}d / {((r.consolidation_range_pct ?? 0) * 100).toFixed(1)}% range</div>
                            )}
                            {r.volume_surge_ratio != null && (
                              <div>Volume: {fmt2(r.volume_surge_ratio)}× avg</div>
                            )}
                            {r.earnings_date && (
                              <div style={{ color: r.earnings_warning ? '#fbbf24' : 'inherit' }}>
                                Earnings: {r.earnings_date}{r.earnings_warning ? ' ⚠ within 10d' : ''}
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
