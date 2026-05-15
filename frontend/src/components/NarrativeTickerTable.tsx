import { useMemo, useState } from 'react'
import type { AcsScore } from '../types/narrative'
import { StageBadge } from './StageBadge'

interface NarrativeTickerTableProps {
  rows: AcsScore[]
  emptyMessage: string
  onSelect?: (ticker: string) => void
}

type SortKey = 'ticker' | 'acs' | 'decay_acs' | 'stage' | 'flags'
type SortDir = 'asc' | 'desc'

interface ColumnDef {
  key: SortKey | null
  label: string
  title?: string
}

const COLUMNS: ColumnDef[] = [
  { key: 'ticker', label: 'Ticker' },
  { key: 'acs', label: 'ACS' },
  { key: 'decay_acs', label: 'Decay', title: 'Time-decayed ACS (λ=0.07/day)' },
  { key: null, label: 'CI' },
  { key: 'stage', label: 'Stage', title: 'Lifecycle stage 1–6 (methodology §4)' },
  { key: null, label: 'A' },
  { key: null, label: 'B' },
  { key: null, label: 'C' },
  { key: null, label: 'D' },
  { key: null, label: 'E' },
  { key: null, label: 'Signal' },
  { key: 'flags', label: 'Flags' },
]

function getSortValue(row: AcsScore, key: SortKey): number | string {
  switch (key) {
    case 'ticker':
      return row.ticker
    case 'acs':
      return row.acs
    case 'decay_acs':
      return row.decay_acs
    case 'stage':
      return row.lifecycle_stage
    case 'flags':
      return row.flags.length
  }
}

export function NarrativeTickerTable({ rows, emptyMessage, onSelect }: NarrativeTickerTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>('acs')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  const sorted = useMemo(() => {
    const copy = [...rows]
    copy.sort((a, b) => {
      const va = getSortValue(a, sortKey)
      const vb = getSortValue(b, sortKey)
      let cmp: number
      if (typeof va === 'number' && typeof vb === 'number') cmp = va - vb
      else cmp = String(va).localeCompare(String(vb))
      return sortDir === 'asc' ? cmp : -cmp
    })
    return copy
  }, [rows, sortKey, sortDir])

  if (rows.length === 0) {
    return <p className="muted">{emptyMessage}</p>
  }

  const onHeaderClick = (key: SortKey | null) => {
    if (key == null) return
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    else {
      setSortKey(key)
      setSortDir(key === 'ticker' ? 'asc' : 'desc')
    }
  }

  return (
    <table className="narrative-table">
      <thead>
        <tr>
          {COLUMNS.map((col) => {
            const sortable = col.key != null
            const active = col.key === sortKey
            const arrow = active ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''
            return (
              <th
                key={col.label}
                title={col.title}
                onClick={sortable ? () => onHeaderClick(col.key) : undefined}
                style={sortable ? { cursor: 'pointer', userSelect: 'none' } : undefined}
                aria-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : undefined}
              >
                {col.label}
                {arrow}
              </th>
            )
          })}
        </tr>
      </thead>
      <tbody>
        {sorted.map((row) => (
          <tr
            key={`${row.ticker}-${row.scored_at}`}
            onClick={onSelect ? () => onSelect(row.ticker) : undefined}
            style={onSelect ? { cursor: 'pointer' } : undefined}
          >
            <td>
              <strong>{row.ticker}</strong>
            </td>
            <td>{row.acs.toFixed(1)}</td>
            <td className="muted" title="Time-decayed ACS (λ=0.07/day)">
              {row.decay_acs.toFixed(1)}
            </td>
            <td className="muted">
              {row.acs_ci_lower.toFixed(0)}–{row.acs_ci_upper.toFixed(0)}
            </td>
            <td>
              <StageBadge stage={row.lifecycle_stage} confidence={row.stage_confidence} />
            </td>
            <td>{row.components.a_attention_persistence.toFixed(1)}</td>
            <td>{row.components.b_contributor_quality.toFixed(1)}</td>
            <td>{row.components.c_narrative_strength.toFixed(1)}</td>
            <td>{row.components.d_thesis_quality.toFixed(1)}</td>
            <td>{row.components.e_market_confirmation.toFixed(1)}</td>
            <td>{row.dominant_signal}</td>
            <td className="muted">{row.flags.join(', ') || '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
