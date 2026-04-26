import type { DitmFilterState } from '../types/ditm'

interface Props {
  filters: DitmFilterState
  onChange: (f: DitmFilterState) => void
}

export function DitmFilterPanel({ filters, onChange }: Props) {
  function set<K extends keyof DitmFilterState>(key: K, value: DitmFilterState[K]) {
    onChange({ ...filters, [key]: value })
  }

  return (
    <div className="filter-panel">
      <span className="filter-label">Filters:</span>

      <label className="filter-item">
        Spread% ≤
        <input type="number" className="filter-number" value={filters.maxSpreadPct}
          min={0} max={100} step={1} onChange={e => set('maxSpreadPct', Number(e.target.value))} />
        <span className="filter-hint">(0 = off)</span>
      </label>

      <label className="filter-item">
        Capital ≤ $
        <input type="number" className="filter-number filter-number-wide" value={filters.maxCapital}
          min={0} step={1000} onChange={e => set('maxCapital', Number(e.target.value))} />
        <span className="filter-hint">(0 = off; mid × 100)</span>
      </label>

      <label className="filter-item filter-toggle">
        <input type="checkbox" checked={filters.smaRatioBullishOnly}
          onChange={e => set('smaRatioBullishOnly', e.target.checked)} />
        SMA50 &gt; SMA200
      </label>

      <label className="filter-item filter-toggle">
        <input type="checkbox" checked={filters.excludeEarningsWithinDte}
          onChange={e => set('excludeEarningsWithinDte', e.target.checked)} />
        Exclude earnings in DTE
      </label>
    </div>
  )
}
