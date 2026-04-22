import type { FilterState } from '../types/screener'

interface Props {
  filters: FilterState
  onChange: (f: FilterState) => void
}

export function FilterPanel({ filters, onChange }: Props) {
  function set<K extends keyof FilterState>(key: K, value: FilterState[K]) {
    onChange({ ...filters, [key]: value })
  }

  return (
    <div className="filter-panel">
      <span className="filter-label">Filters:</span>

      <label className="filter-item">
        RSI min
        <input type="number" className="filter-number" value={filters.minRsi}
          min={0} max={100} step={5} onChange={e => set('minRsi', Number(e.target.value))} />
      </label>

      <label className="filter-item">
        RSI max
        <input type="number" className="filter-number" value={filters.maxRsi}
          min={0} max={100} step={5} onChange={e => set('maxRsi', Number(e.target.value))} />
      </label>

      <label className="filter-item">
        IV Rank ≥
        <input type="number" className="filter-number" value={filters.minIvRank}
          min={0} max={100} step={5} onChange={e => set('minIvRank', Number(e.target.value))} />
        <span className="filter-hint">(0 = off)</span>
      </label>

      <label className="filter-item">
        Delta min
        <input type="number" className="filter-number" value={filters.minDelta}
          min={-1} max={0} step={0.05} onChange={e => set('minDelta', Number(e.target.value))} />
        <span className="filter-hint">(e.g. −0.30)</span>
      </label>

      <label className="filter-item">
        Delta max
        <input type="number" className="filter-number" value={filters.maxDelta}
          min={-1} max={0} step={0.05} onChange={e => set('maxDelta', Number(e.target.value))} />
        <span className="filter-hint">(e.g. −0.05)</span>
      </label>

      <label className="filter-item">
        Spread% ≤
        <input type="number" className="filter-number" value={filters.maxSpreadPct}
          min={0} max={100} step={1} onChange={e => set('maxSpreadPct', Number(e.target.value))} />
        <span className="filter-hint">(0 = off)</span>
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
