import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
  createColumnHelper,
  type SortingState,
} from '@tanstack/react-table'
import { useState } from 'react'
import type { DitmResult } from '../types/ditm'

const col = createColumnHelper<DitmResult>()

function fmt2(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(2)
}
function fmtMoney(n: number | null | undefined): string {
  if (n == null) return '—'
  return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })
}
function fmtPct(n: number | null | undefined, digits = 2): string {
  if (n == null) return '—'
  return n.toFixed(digits) + '%'
}

const COLUMNS = [
  col.accessor('symbol', {
    header: 'Symbol',
    cell: info => <strong>{info.getValue()}</strong>,
  }),
  col.accessor('price', {
    header: 'Price',
    cell: info => fmt2(info.getValue()),
  }),
  col.accessor('sma_ratio', {
    header: () => (
      <span className="col-tip" title="SMA50 / SMA200  ·  >1.0 = bullish  ·  <1.0 = bearish">
        SMA50/200 ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      if (v == null || isNaN(v)) return <span className="dim">—</span>
      return <span className={v >= 1 ? 'positive' : 'negative'}>{v.toFixed(4)}</span>
    },
  }),
  col.accessor('rsi', {
    header: () => (
      <span className="col-tip" title="RSI(14)  ·  <30 oversold (buy signal)  ·  >70 overbought (avoid)  ·  40–65 ideal">
        RSI(14) ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      if (v == null || isNaN(v)) return <span className="dim">—</span>
      const cls = v >= 70 ? 'rsi-high' : v <= 30 ? 'rsi-low' : 'rsi-ok'
      return <span className={cls}>{v.toFixed(1)}</span>
    },
  }),
  col.accessor('iv_rank', {
    header: () => (
      <span className="col-tip" title="IV Rank (HV proxy)  ·  Low = cheap vol = less extrinsic cost  ·  Ideal for buying calls">
        IV Rank ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      const pct = info.row.original.iv_percentile
      if (v == null) return <span className="dim">N/A</span>
      const cls = v <= 30 ? 'badge badge-green' : v <= 50 ? 'badge badge-yellow' : 'badge badge-gray'
      return (
        <span>
          <span className={cls}>{v.toFixed(0)}</span><br />
          <span className="expiry-date">P:{pct != null ? pct.toFixed(0) : '—'}</span>
        </span>
      )
    },
  }),
  col.accessor('earnings_date', {
    header: 'Earnings',
    cell: info => {
      const row = info.row.original
      if (!row.earnings_date) return <span className="dim">—</span>
      return (
        <span className={row.earnings_within_dte ? 'earnings-warn' : ''}>
          {row.earnings_date}{row.earnings_within_dte && ' ⚠'}
        </span>
      )
    },
  }),
  col.accessor('strike', {
    header: 'Strike',
    cell: info => {
      const row = info.row.original
      return (
        <span className={row.strike_is_fallback ? 'fallback' : ''}>
          {fmt2(info.getValue())}
          {row.strike_is_fallback && ' *'}
        </span>
      )
    },
  }),
  col.accessor('dte', {
    header: 'DTE',
    cell: info => (
      <span>
        {info.getValue()}<br />
        <span className="expiry-date">{info.row.original.expiration}</span>
      </span>
    ),
  }),
  col.accessor('premium', {
    header: () => (
      <span className="col-tip" title="Mid-price of the call option (cost per share)">
        Premium ⓘ
      </span>
    ),
    cell: info => fmt2(info.getValue()),
  }),
  col.accessor('delta', {
    header: () => (
      <span className="col-tip" title="BS call delta  ·  ≥0.80 = deep ITM  ·  Tracks ~80%+ of stock move">
        Delta ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      const cls = v >= 0.80 ? 'delta-ok' : 'delta-warn'
      return <span className={cls}>{v.toFixed(3)}</span>
    },
  }),
  col.accessor('extrinsic_pct', {
    header: () => (
      <span className="col-tip" title="(Premium − Intrinsic) / Stock Price × 100  ·  This is the real cost of leverage  ·  Lower = better">
        Extrinsic% ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      const cls = v <= 2 ? 'positive' : v <= 4 ? '' : 'negative'
      return (
        <span>
          <span className={cls}>{fmtPct(v, 2)}</span><br />
          <span className="expiry-date">${info.row.original.extrinsic_value.toFixed(2)}</span>
        </span>
      )
    },
  }),
  col.accessor('moneyness_pct', {
    header: () => (
      <span className="col-tip" title="(Stock − Strike) / Stock × 100  ·  Higher = deeper ITM = lower gamma risk">
        Moneyness% ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      const cls = v >= 15 ? 'positive' : v >= 8 ? '' : 'rsi-high'
      return <span className={cls}>{fmtPct(v, 1)}</span>
    },
  }),
  col.accessor('leverage_ratio', {
    header: () => (
      <span className="col-tip" title="Stock Price / Premium  ·  How many $ of stock exposure per $ invested  ·  3–8× is typical">
        Leverage ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      const cls = v >= 3 && v <= 8 ? 'positive' : 'delta-warn'
      return <span className={cls}>{v.toFixed(2)}×</span>
    },
  }),
  col.accessor('breakeven_pct_above', {
    header: () => (
      <span className="col-tip" title="% stock must rise above current price to break even at expiry  ·  Lower = better">
        B/E Above% ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      const cls = v <= 2 ? 'positive' : v <= 5 ? '' : 'negative'
      return (
        <span>
          <span className={cls}>{fmtPct(v, 2)}</span><br />
          <span className="expiry-date">${info.row.original.breakeven_price.toFixed(2)}</span>
        </span>
      )
    },
  }),
  col.accessor('capital_at_risk', {
    header: () => (
      <span className="col-tip" title="Premium × 100 = max loss per contract if stock goes to zero">
        Capital/Contract ⓘ
      </span>
    ),
    cell: info => (
      <span>
        {fmtMoney(info.getValue())}<br />
        <span className="expiry-date">{fmtPct(info.row.original.vs_stock_cost_pct, 1)} of stock</span>
      </span>
    ),
  }),
  col.accessor('bid_ask_spread_pct', {
    header: () => (
      <span className="col-tip" title="(Ask − Bid) / Mid × 100  ·  >10% = illiquid — widen exit costs">
        Spread% ⓘ
      </span>
    ),
    cell: info => {
      const v = info.getValue()
      if (v == null) return <span className="dim">—</span>
      const cls = v > 10 ? 'spread-wide' : v > 5 ? 'spread-ok' : 'spread-tight'
      return <span className={cls}>{v.toFixed(1)}%</span>
    },
  }),
  col.accessor('open_interest', {
    header: 'OI',
    cell: info => {
      const v = info.getValue()
      if (v == null) return <span className="dim">—</span>
      const cls = v >= 100 ? '' : 'delta-warn'
      return <span className={cls}>{v.toLocaleString()}</span>
    },
  }),
]

interface Props {
  data: DitmResult[]
}

export function DitmTable({ data }: Props) {
  const [sorting, setSorting] = useState<SortingState>([
    { id: 'extrinsic_pct', desc: false },
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
        * Strike is a fallback (no call met delta threshold). Extrinsic% = (Premium − Intrinsic) / Stock Price × 100. IV Rank = HV-based proxy.
      </p>
    </div>
  )
}
