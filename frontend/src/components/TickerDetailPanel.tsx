import type { TickerDetail } from '../types/narrative'
import { Sparkline } from './Sparkline'
import { StageBadge } from './StageBadge'
import { TierMixBar } from './TierMixBar'

interface TickerDetailPanelProps {
  detail: TickerDetail | null
  loading?: boolean
  error?: string | null
  onClose?: () => void
}

const fmtPct = (v: number | null | undefined, digits = 0) =>
  v == null ? '—' : `${(v * 100).toFixed(digits)}%`
const fmtNum = (v: number | null | undefined, digits = 2) =>
  v == null ? '—' : v.toFixed(digits)

export function TickerDetailPanel({ detail, loading, error, onClose }: TickerDetailPanelProps) {
  if (loading) {
    return (
      <aside className="ticker-detail-panel">
        <div className="panel-header">
          <h3>Loading…</h3>
          {onClose && <button onClick={onClose}>×</button>}
        </div>
      </aside>
    )
  }
  if (error) {
    return (
      <aside className="ticker-detail-panel">
        <div className="panel-header">
          <h3>Lookup failed</h3>
          {onClose && <button onClick={onClose}>×</button>}
        </div>
        <p role="alert">{error}</p>
      </aside>
    )
  }
  if (!detail) return null

  const s = detail.score
  return (
    <aside className="ticker-detail-panel">
      <div className="panel-header">
        <h3>
          {detail.ticker}{' '}
          <StageBadge stage={s.lifecycle_stage} confidence={s.stage_confidence} />
        </h3>
        {onClose && (
          <button onClick={onClose} aria-label="Close detail panel">
            ×
          </button>
        )}
      </div>

      <div className="panel-row">
        <span className="label">ACS</span>
        <span className="value">
          {s.acs.toFixed(1)}{' '}
          <span style={{ opacity: 0.7, fontSize: '0.85em' }}>
            (CI {s.acs_ci_lower.toFixed(0)}–{s.acs_ci_upper.toFixed(0)})
          </span>
        </span>
      </div>

      <div className="panel-row">
        <span className="label">Dominant signal</span>
        <span className="value">{s.dominant_signal || '—'}</span>
      </div>

      <div className="panel-row">
        <span className="label">14-day mentions</span>
        <span className="value">
          <Sparkline buckets={detail.daily_buckets} />
          <span style={{ marginLeft: 8 }}>
            {detail.mentions_14d} · {detail.unique_authors_14d} authors
          </span>
        </span>
      </div>

      <div className="panel-row">
        <span className="label">Tier mix</span>
        <span className="value">
          <TierMixBar tier1={detail.tier1_pct} tier2={detail.tier2_pct} tier3={detail.tier3_pct} />
        </span>
      </div>

      <div className="panel-row">
        <span className="label">Gini (14d)</span>
        <span className="value">{fmtNum(detail.gini_14d)}</span>
      </div>

      <div className="panel-row">
        <span className="label">Contributor growth (7d)</span>
        <span className="value">{fmtPct(detail.contributor_count_growth_7d)}</span>
      </div>

      <h4>Conviction</h4>
      <div className="panel-row">
        <span className="label">Researched bull</span>
        <span className="value">{fmtPct(detail.conviction_researched_bull_ratio)}</span>
      </div>
      <div className="panel-row">
        <span className="label">Researched bear</span>
        <span className="value">{fmtPct(detail.conviction_researched_bear_ratio)}</span>
      </div>
      <div className="panel-row">
        <span className="label">Emotional bull</span>
        <span className="value">{fmtPct(detail.conviction_emotional_bull_ratio)}</span>
      </div>
      <div className="panel-row">
        <span className="label">DD intensity (norm)</span>
        <span className="value">{fmtNum(detail.conviction_dd_norm)}</span>
      </div>
      <div className="panel-row">
        <span className="label">Classified posts</span>
        <span className="value">{detail.conviction_classified_14d ?? '—'}</span>
      </div>

      {s.flags.length > 0 && (
        <>
          <h4>Flags</h4>
          <div className="flag-list">
            {s.flags.map((f) => (
              <code key={f} className="flag-chip">
                {f}
              </code>
            ))}
          </div>
        </>
      )}
    </aside>
  )
}
