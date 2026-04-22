import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
  createColumnHelper,
  type SortingState,
} from '@tanstack/react-table'
import { useState, useMemo } from 'react'
import type { ScreenerResult, GroupedScreenerResult } from '../types/screener'

const col = createColumnHelper<GroupedScreenerResult>()

function fmt2(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(2)
}
function fmtAnn(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(1) + '%'
}
function fmtDelta(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(3)
}

// Ticker-level columns — for header rendering + sorting only.
// Cells are rendered manually in the tbody via rowSpan.
const COLUMNS = [
  col.accessor('symbol', { header: 'Symbol', cell: () => null }),
  col.accessor('price', { header: 'Price', cell: () => null }),
  col.accessor('bb_lower', {
    header: () => (
      <span className="col-tip" title="Bollinger Bands (20, 2σ)  ·  Upper / Middle / Lower">
        BB Bands ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('vol_support_1', {
    header: 'Vol Support',
    cell: () => null,
    enableSorting: false,
  }),
  col.accessor('sma_ratio', {
    header: () => (
      <span className="col-tip" title="SMA50 / SMA200  ·  >1.0 = bullish (50 above 200)  ·  <1.0 = bearish">
        SMA50/200 ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('rsi', {
    header: () => (
      <span className="col-tip" title="RSI(14) Wilder-smoothed  ·  >70 overbought  ·  <30 oversold  ·  40–70 ideal for CSP">
        RSI(14) ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('iv_rank', {
    header: () => (
      <span className="col-tip" title="IV Rank = (HV_today − HV_min_252) / (HV_max_252 − HV_min_252) × 100  ·  HV-based proxy  ·  High = selling expensive vol">
        IV Rank ⓘ
      </span>
    ),
    cell: () => null,
  }),
  col.accessor('earnings_date', { header: 'Earnings', cell: () => null }),
  // Hidden sort key — excluded from visible headers via columnVisibility
  col.accessor('best_score', { header: () => null, cell: () => null }),
]

function groupResults(results: ScreenerResult[]): GroupedScreenerResult[] {
  const map = new Map<string, GroupedScreenerResult>()
  for (const r of results) {
    if (!map.has(r.symbol)) {
      map.set(r.symbol, {
        symbol: r.symbol,
        price: r.price,
        bb_upper: r.bb_upper,
        bb_middle: r.bb_middle,
        bb_lower: r.bb_lower,
        sma_ratio: r.sma_ratio,
        rsi: r.rsi,
        iv_rank: r.iv_rank,
        iv_percentile: r.iv_percentile,
        earnings_date: r.earnings_date,
        earnings_within_dte: false,
        vol_support_1: r.vol_support_1,
        vol_support_2: r.vol_support_2,
        vol_support_3: r.vol_support_3,
        best_score: 0,
        expirations: [],
      })
    }
    const group = map.get(r.symbol)!
    if (r.earnings_within_dte) group.earnings_within_dte = true
    group.expirations.push({
      dte: r.dte,
      expiration: r.expiration,
      earnings_within_dte: r.earnings_within_dte,
      strike: r.strike,
      strike_is_fallback: r.strike_is_fallback,
      strike_mid: r.strike_mid,
      strike_mid_is_fallback: r.strike_mid_is_fallback,
      delta: r.delta,
      delta_mid: r.delta_mid,
      bid_ask_spread_pct: r.bid_ask_spread_pct,
      bid_ask_spread_pct_mid: r.bid_ask_spread_pct_mid,
      premium: r.premium,
      premium_mid: r.premium_mid,
      collateral: r.collateral,
      collateral_mid: r.collateral_mid,
      return_pct: r.return_pct,
      return_pct_mid: r.return_pct_mid,
      annualized_return: r.annualized_return,
      annualized_return_mid: r.annualized_return_mid,
      csp_score: r.csp_score,
      csp_score_mid: r.csp_score_mid,
    })
  }
  for (const g of map.values()) {
    g.expirations.sort((a, b) => a.dte - b.dte)
    g.best_score = Math.max(...g.expirations.map(e => e.csp_score))
  }
  return [...map.values()].sort((a, b) => b.best_score - a.best_score)
}

interface Props {
  data: ScreenerResult[]
}

export function ScreenerTable({ data }: Props) {
  const groupedData = useMemo(() => groupResults(data), [data])
  const [sorting, setSorting] = useState<SortingState>([{ id: 'best_score', desc: true }])
  const [altExpanded, setAltExpanded] = useState<Set<string>>(new Set())

  const toggleAlt = (key: string) => {
    setAltExpanded(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key); else next.add(key)
      return next
    })
  }

  const table = useReactTable({
    data: groupedData,
    columns: COLUMNS,
    state: { sorting, columnVisibility: { best_score: false } },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })

  if (groupedData.length === 0) return null

  const scoreCol = table.getColumn('best_score')
  const scoreSorted = scoreCol?.getIsSorted()

  const fmtSpread = (v: number | null) => {
    if (v == null) return <span>—</span>
    const cls = v > 10 ? 'spread-wide' : v > 5 ? 'spread-ok' : 'spread-tight'
    return <span className={cls}>{v.toFixed(1)}%</span>
  }
  const scoreFmt = (v: number) => {
    const cls = v >= 70 ? 'score-good' : v >= 45 ? 'score-caution' : 'score-bad'
    return <span className={cls}>{v.toFixed(0)}</span>
  }

  return (
    <div className="table-wrapper">
      <table className="screener-table">
        <thead>
          {table.getHeaderGroups().map(hg => (
            <tr key={hg.id}>
              {hg.headers.map(header => (
                <th
                  key={header.id}
                  onClick={header.column.getToggleSortingHandler()}
                  className={header.column.getCanSort() ? 'sortable' : ''}
                >
                  {flexRender(header.column.columnDef.header, header.getContext())}
                  {header.column.getIsSorted() === 'asc' && ' ↑'}
                  {header.column.getIsSorted() === 'desc' && ' ↓'}
                </th>
              ))}
              {/* DTE-section headers */}
              <th>DTE</th>
              <th>
                <span className="col-tip" title="BB Low strike (≤ BB Lower)  ·  ▼ alt reveals BB Mid strike (≤ BB Middle)">
                  Strike ⓘ
                </span>
              </th>
              <th>Delta</th>
              <th>
                <span className="col-tip" title="(Ask − Bid) / Mid × 100  ·  Lower = tighter market  ·  >10% = illiquid">
                  Spread% ⓘ
                </span>
              </th>
              <th>Ann. Return</th>
              <th
                className="sortable"
                onClick={() => scoreCol?.toggleSorting(scoreSorted === 'asc')}
              >
                <span className="col-tip" title="CSP score 0-100: IV Rank(25) + Ann.Return(20) + SMA trend(20) + RSI zone(15) + Delta(10) + Spread%(10) − Earnings(−15)">
                  Score ⓘ
                </span>
                {scoreSorted === 'asc' && ' ↑'}
                {scoreSorted === 'desc' && ' ↓'}
              </th>
            </tr>
          ))}
        </thead>

        {table.getRowModel().rows.map(row => {
          const r = row.original
          const nExp = r.expirations.length
          return (
            <tbody
              key={r.symbol}
              className={`ticker-group${r.earnings_within_dte ? ' group-earnings-warn' : ''}`}
            >
              {r.expirations.map((exp, expIdx) => {
                const altKey = `${r.symbol}-${exp.expiration}`
                const showAlt = altExpanded.has(altKey)
                const isFirst = expIdx === 0
                return (
                  <tr key={expIdx} className={isFirst ? 'first-exp-row' : 'sub-exp-row'}>

                    {/* ── Ticker-level cells (rowSpan covers all DTE rows) ── */}
                    {isFirst && <>
                      <td rowSpan={nExp} className="ticker-cell">
                        <strong>{r.symbol}</strong>
                      </td>
                      <td rowSpan={nExp}>{fmt2(r.price)}</td>
                      <td rowSpan={nExp}>
                        <span className="bb-bands">
                          <span className="bb-upper">{fmt2(r.bb_upper)}</span>
                          <span className="bb-middle">{fmt2(r.bb_middle)}</span>
                          <span className="bb-lower">{fmt2(r.bb_lower)}</span>
                        </span>
                      </td>
                      <td rowSpan={nExp}>
                        {(() => {
                          const levels = [r.vol_support_1, r.vol_support_2, r.vol_support_3]
                            .filter((v): v is number => v != null)
                          if (levels.length === 0) return <span className="dim">—</span>
                          return (
                            <span className="vol-support">
                              {levels.map((lvl, i) => (
                                <span key={i} className="vol-support-level">
                                  {fmt2(lvl)}
                                  <span className="vol-support-pct"> {((lvl - r.price) / r.price * 100).toFixed(1)}%</span>
                                </span>
                              ))}
                            </span>
                          )
                        })()}
                      </td>
                      <td rowSpan={nExp}>
                        {r.sma_ratio == null || isNaN(r.sma_ratio)
                          ? <span className="dim">—</span>
                          : <span className={r.sma_ratio >= 1 ? 'positive' : 'negative'}>{r.sma_ratio.toFixed(4)}</span>
                        }
                      </td>
                      <td rowSpan={nExp}>
                        {r.rsi == null || isNaN(r.rsi)
                          ? <span className="dim">—</span>
                          : <span className={r.rsi >= 70 ? 'rsi-high' : r.rsi <= 30 ? 'rsi-low' : 'rsi-ok'}>{r.rsi.toFixed(1)}</span>
                        }
                      </td>
                      <td rowSpan={nExp}>
                        {r.iv_rank == null
                          ? <span className="dim">N/A</span>
                          : <>
                              <span className={r.iv_rank >= 50 ? 'badge badge-green' : r.iv_rank >= 30 ? 'badge badge-yellow' : 'badge badge-red'}>
                                {r.iv_rank.toFixed(0)}
                              </span><br />
                              <span className="expiry-date">P:{r.iv_percentile != null ? r.iv_percentile.toFixed(0) : '—'}</span>
                            </>
                        }
                      </td>
                      <td rowSpan={nExp}>
                        {r.earnings_date
                          ? <span className={r.earnings_within_dte ? 'earnings-warn' : ''}>{r.earnings_date}{r.earnings_within_dte && ' ⚠'}</span>
                          : <span className="dim">—</span>
                        }
                      </td>
                    </>}

                    {/* ── DTE-level cells (one row per expiration) ── */}
                    <td className="dte-cell">
                      <span className="dte-num">{exp.dte}</span><br />
                      <span className="expiry-date">{exp.expiration}</span>
                      {exp.earnings_within_dte && <span className="earnings-warn"> ⚠</span>}
                    </td>

                    <td className="strike-cell">
                      <span className="strike-primary">
                        <span className={exp.strike_is_fallback ? 'fallback' : ''}>
                          {fmt2(exp.strike)}{exp.strike_is_fallback && ' *'}
                        </span>
                        <span className="strike-fall"> {((exp.strike - r.price) / r.price * 100).toFixed(1)}%</span>
                      </span>
                      {showAlt && (
                        <span className="strike-alt">
                          <span className={exp.strike_mid_is_fallback ? 'fallback' : ''}>
                            {fmt2(exp.strike_mid)}{exp.strike_mid_is_fallback && ' *'}
                          </span>
                          <span className="strike-fall"> {((exp.strike_mid - r.price) / r.price * 100).toFixed(1)}%</span>
                        </span>
                      )}
                      <button
                        className="alt-toggle"
                        onClick={() => toggleAlt(altKey)}
                        title={showAlt ? 'Hide BB Mid strike' : 'Show BB Mid alt strike (≤ BB Middle)'}
                      >
                        {showAlt ? '▲ hide' : '▼ alt'}
                      </button>
                    </td>

                    <td>
                      <span className={exp.delta >= -0.30 && exp.delta <= -0.15 ? 'delta-ok' : 'delta-warn'}>
                        {fmtDelta(exp.delta)}
                      </span>
                      {showAlt && (
                        <span className={`alt-row ${exp.delta_mid >= -0.30 && exp.delta_mid <= -0.15 ? 'delta-ok' : 'delta-warn'}`}>
                          {fmtDelta(exp.delta_mid)}
                        </span>
                      )}
                    </td>

                    <td>
                      {fmtSpread(exp.bid_ask_spread_pct)}
                      {showAlt && <span className="alt-row">{fmtSpread(exp.bid_ask_spread_pct_mid)}</span>}
                    </td>

                    <td>
                      {fmtAnn(exp.annualized_return)}
                      {showAlt && <span className="alt-row">{fmtAnn(exp.annualized_return_mid)}</span>}
                    </td>

                    <td>
                      {scoreFmt(exp.csp_score)}
                      {showAlt && <span className="alt-row">{scoreFmt(exp.csp_score_mid)}</span>}
                    </td>

                  </tr>
                )
              })}
            </tbody>
          )
        })}
      </table>
      <p className="table-note">
        * Strike is a fallback (no put ≤ BB Lower). IV Rank/Percentile = HV-based proxy (252-day window). P: = IV Percentile.
      </p>
    </div>
  )
}
