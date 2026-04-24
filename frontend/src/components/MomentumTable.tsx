import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
  createColumnHelper,
  type SortingState,
} from '@tanstack/react-table'
import { useState } from 'react'
import type { MomentumResult } from '../types/momentum'

const col = createColumnHelper<MomentumResult>()

function fmtNum(n: number | null | undefined, digits = 2): string {
  if (n == null || isNaN(n)) return '—'
  return n.toFixed(digits)
}
function fmtPct(n: number | null | undefined, digits = 2, showPlus = false): string {
  if (n == null || isNaN(n)) return '—'
  const prefix = showPlus && n > 0 ? '+' : ''
  return prefix + n.toFixed(digits) + '%'
}

const COLUMNS = [
  col.accessor('symbol', {
    header: 'Symbol',
    cell: info => <strong>{info.getValue()}</strong>,
  }),
  col.accessor('momentum_score', {
    header: () => (
      <span className="col-tip" title="Composite momentum score 0–100 combining relative volume, RSI zone, 52-week proximity, SMA structure, and rate of change">
        Score ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      const cls = v >= 70 ? 'positive' : v >= 45 ? 'rsi-ok' : 'negative'
      return <span className={cls} style={{ fontWeight: 700, fontSize: '15px' }}>{v.toFixed(0)}</span>
    },
  }),
  col.accessor('price', {
    header: 'Price',
    cell: info => '$' + info.getValue().toFixed(2),
  }),
  col.accessor('price_change_1d_pct', {
    header: '1d Chg',
    cell: info => {
      const v = info.getValue()
      if (v == null) return <span className="dim">—</span>
      return <span className={v >= 0 ? 'positive' : 'negative'}>{v > 0 ? '+' : ''}{v.toFixed(2)}%</span>
    },
  }),
  col.accessor('rvol', {
    header: () => (
      <span className="col-tip" title="Relative Volume · Today's volume ÷ 20-day average volume · Values above 1.0 indicate above-average activity">
        RVOL ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      if (v == null) return <span className="dim">—</span>
      const cls = v >= 3 ? 'positive' : v >= 1.5 ? 'rsi-ok' : ''
      return <span className={cls}>{v.toFixed(2)}×</span>
    },
  }),
  col.accessor('rsi', {
    header: () => (
      <span className="col-tip" title="Relative Strength Index (14-period) · Momentum oscillator on a 0–100 scale · >70 overbought · <30 oversold">
        RSI(14) ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      if (v == null) return <span className="dim">—</span>
      const cls = v >= 80 ? 'rsi-high' : (v >= 55 && v <= 72) ? 'rsi-ok' : v <= 30 ? 'rsi-low' : ''
      return <span className={cls}>{v.toFixed(1)}</span>
    },
  }),
  col.accessor('roc_21', {
    header: () => (
      <span className="col-tip" title="21-day Rate of Change · % price change over the past month">
        ROC(21) ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      if (v == null) return <span className="dim">—</span>
      const cls = v >= 10 ? 'positive' : v >= 0 ? 'rsi-ok' : 'negative'
      return <span className={cls}>{fmtPct(v, 1, true)}</span>
    },
  }),
  col.accessor('dist_from_52w_high_pct', {
    header: () => (
      <span className="col-tip" title="Distance from the 52-week high · 0% = at the high · Negative = % below the high">
        vs 52w High ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      const row = info.row.original
      if (v == null) return <span className="dim">—</span>
      const cls = v >= -5 ? 'positive' : v >= -15 ? 'rsi-ok' : ''
      return (
        <span>
          <span className={cls}>{fmtPct(v, 1, true)}</span><br />
          <span className="expiry-date">Hi: ${row.high_52w.toFixed(2)}</span>
        </span>
      )
    },
  }),
  col.accessor('sma_ratio', {
    header: () => (
      <span className="col-tip" title="SMA50 ÷ SMA200 · Ratio >1 means the 50-day average is above the 200-day average (bullish structure)">
        SMA50/200 ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      if (v == null) return <span className="dim">—</span>
      return <span className={v >= 1 ? 'positive' : 'negative'}>{v.toFixed(4)}</span>
    },
  }),
  col.accessor('price_vs_sma20_pct', {
    header: () => (
      <span className="col-tip" title="% above or below the 20-day moving average · Positive = price is above SMA20">
        vs SMA20 ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      if (v == null) return <span className="dim">—</span>
      const cls = v > 0 && v <= 5 ? 'positive' : v > 10 ? 'rsi-high' : v < 0 ? 'negative' : ''
      return <span className={cls}>{fmtPct(v, 1, true)}</span>
    },
  }),
  col.accessor('dist_from_sma200_pct', {
    header: () => (
      <span className="col-tip" title="% above or below the 200-day moving average · Positive = price is above SMA200">
        vs SMA200 ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      if (v == null) return <span className="dim">—</span>
      const cls = v >= 10 && v <= 40 ? 'positive' : v > 40 ? 'rsi-high' : 'negative'
      return <span className={cls}>{fmtPct(v, 1, true)}</span>
    },
  }),
  col.accessor('sma20_slope_pct', {
    header: () => (
      <span className="col-tip" title="% change in SMA20 over the last 5 trading days · Positive = short-term trend is rising">
        SMA20 Slope ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      if (v == null) return <span className="dim">—</span>
      const cls = v > 0 ? 'positive' : 'negative'
      return <span className={cls}>{fmtPct(v, 3, true)}</span>
    },
  }),
  col.accessor('macd_histogram', {
    header: () => (
      <span className="col-tip" title="MACD histogram (12, 26, 9) · Difference between the MACD line and its signal line · Positive = bullish momentum">
        MACD Hist ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      if (v == null) return <span className="dim">—</span>
      const cls = v > 0 ? 'positive' : 'negative'
      return <span className={cls}>{v > 0 ? '+' : ''}{fmtNum(v, 4)}</span>
    },
  }),
  col.accessor('short_ratio', {
    header: () => (
      <span className="col-tip" title="Short Interest Ratio · Days to cover the short interest at the stock's average daily volume">
        Short Ratio ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      if (v == null) return <span className="dim">—</span>
      const cls = v >= 5 ? 'rsi-high' : ''
      return <span className={cls}>{v.toFixed(1)}d</span>
    },
  }),
  col.accessor('low_52w', {
    header: '52w Lo',
    cell: info => '$' + info.getValue().toFixed(2),
  }),
]

interface Props {
  data: MomentumResult[]
}

export function MomentumTable({ data }: Props) {
  const [sorting, setSorting] = useState<SortingState>([
    { id: 'momentum_score', desc: true },
  ])

  const table = useReactTable({
    data,
    columns: COLUMNS,
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
                  onClick={h.column.getToggleSortingHandler()}
                  className={h.column.getCanSort() ? 'sortable' : ''}
                >
                  {flexRender(h.column.columnDef.header, h.getContext())}
                  {h.column.getIsSorted() === 'asc' ? ' ▲' : h.column.getIsSorted() === 'desc' ? ' ▼' : ''}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map(row => (
            <tr key={row.id}>
              {row.getVisibleCells().map(cell => (
                <td key={cell.id}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="table-note">
        Score: RVOL(30pt) + RSI zone(20pt) + 52w proximity(25pt) + SMA50/200(15pt) + ROC(10pt). Sorted by Score descending.
      </p>
    </div>
  )
}
