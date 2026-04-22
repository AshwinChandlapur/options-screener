import type { MomentumFilterState } from '../types/momentum'

interface Props {
  filters: MomentumFilterState
  onChange: (f: MomentumFilterState) => void
}

export function MomentumFilterPanel({ filters, onChange }: Props) {
  function set<K extends keyof MomentumFilterState>(key: K, value: MomentumFilterState[K]) {
    onChange({ ...filters, [key]: value })
  }

  return (
    <div className="filter-panel">
      <span className="filter-label">Filters:</span>

      <label className="filter-item">
        Score ≥
        <input type="number" className="filter-number" value={filters.minScore}
          min={0} max={100} step={5}
          onChange={e => set('minScore', Number(e.target.value))} />
        <span className="filter-hint">(0 = off)</span>
      </label>

      <label className="filter-item">
        RVOL ≥
        <input type="number" className="filter-number" value={filters.minRvol}
          min={0} max={10} step={0.5}
          onChange={e => set('minRvol', Number(e.target.value))} />
        <span className="filter-hint">(0 = off, 1.5 = 50% above avg)</span>
      </label>

      <label className="filter-item">
        RSI min
        <input type="number" className="filter-number" value={filters.minRsi}
          min={0} max={100} step={5}
          onChange={e => set('minRsi', Number(e.target.value))} />
      </label>

      <label className="filter-item">
        RSI max
        <input type="number" className="filter-number" value={filters.maxRsi}
          min={0} max={100} step={5}
          onChange={e => set('maxRsi', Number(e.target.value))} />
      </label>

      <label className="filter-item">
        ROC(21) ≥ %
        <input type="number" className="filter-number" value={filters.minRoc21}
          min={-100} max={200} step={1}
          onChange={e => set('minRoc21', Number(e.target.value))} />
        <span className="filter-hint">(0 = off)</span>
      </label>

      <label className="filter-item">
        Within % of 52w High
        <input type="number" className="filter-number" value={filters.maxDistFrom52wHigh}
          min={0} max={100} step={5}
          onChange={e => set('maxDistFrom52wHigh', Number(e.target.value))} />
        <span className="filter-hint">(0 = off, 15 = within 15%)</span>
      </label>

      <label className="filter-item filter-toggle">
        <input type="checkbox" checked={filters.smaRatioBullishOnly}
          onChange={e => set('smaRatioBullishOnly', e.target.checked)} />
        SMA50 &gt; SMA200
      </label>
    </div>
  )
}
