import type { TickerDetail } from '../types/narrative'
import { labelSignal } from '../constants/narrative'
import { Sparkline } from './Sparkline'
import { StageBadge } from './StageBadge'
import { TierMixBar } from './TierMixBar'

interface TickerDetailPanelProps {
  detail: TickerDetail | null
  loading?: boolean
  error?: string | null
  onClose?: () => void
}

const FLAG_LABELS: Record<string, string> = {
  gini_high:          'Concentrated posts \u2014 a few accounts dominate the discussion',
  decelerating_3d:    'Fading momentum \u2014 mention rate has dropped 3 days in a row',
  late_stage:         'Late stage \u2014 narrative past the ideal entry window',
  small_cap:          'Small cap \u2014 extra caution advised on liquidity',
  small_cap_haircut:  'Small cap \u2014 extra caution advised on liquidity',
  low_unique_authors: 'Few authors \u2014 not enough distinct voices yet',
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
        <span className="label">Narrative score</span>
        <span className="value">
          {s.acs.toFixed(1)}{' '}
          <span style={{ opacity: 0.7, fontSize: '0.85em' }}>
            (range {s.acs_ci_lower.toFixed(0)}–{s.acs_ci_upper.toFixed(0)})
          </span>
        </span>
      </div>

      <div className="panel-row">
        <span className="label" title="The most common type of discussion in the last 14 days. Direction (Bullish/Bearish) × style (Analytical = data-backed research; Hype-driven = price momentum, FOMO, excitement)">Dominant signal</span>
        <span className="value">{labelSignal(s.dominant_signal)}</span>
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

      <h4>What are people saying?</h4>
      {detail.conviction_bull_share !== null ? (
        <>
          <div className="panel-row">
            <span className="label" title="Share of classified posts where the author leans bullish (ADR-0020 direction axis).">Direction — bull</span>
            <span className="value">{fmtPct(detail.conviction_bull_share)}</span>
          </div>
          <div className="panel-row">
            <span className="label" title="Share of classified posts that contain substantive analysis vs hype (ADR-0020 substance axis).">Substance — researched</span>
            <span className="value">{fmtPct(detail.conviction_researched_share)}</span>
          </div>
          <div className="panel-row">
            <span className="label" title="Share of classified posts where the author is entering a new position (ADR-0020 position axis).">Position — entering</span>
            <span className="value">{fmtPct(detail.conviction_entering_share)}</span>
          </div>
          <div className="panel-row">
            <span className="label" title="Share of classified posts where the author is exiting a position (ADR-0020 position axis).">Position — exiting</span>
            <span className="value">{fmtPct(detail.conviction_exiting_share)}</span>
          </div>
          <div className="panel-row">
            <span className="label" title="Most-common driver across classified posts: earnings, product, macro, flows, valuation, or other (ADR-0020 driver axis).">Top driver</span>
            <span className="value">{detail.conviction_driver_top ?? '—'}</span>
          </div>
          <div className="panel-row">
            <span className="label" title="Joint share: posts that are both bullish AND substantive. Drives ACS Component D (ADR-0021).">Bullish — analytical</span>
            <span className="value">{fmtPct(detail.conviction_bull_researched_share)}</span>
          </div>
          <div className="panel-row">
            <span className="label" title="Joint share: posts that are both bearish AND substantive. Drives ACS Component D (ADR-0021).">Bearish — analytical</span>
            <span className="value">{fmtPct(detail.conviction_bear_researched_share)}</span>
          </div>
          <div className="panel-row">
            <span className="label" title="Number of posts that have been classified. Fewer than 10 makes percentages unreliable.">Posts analysed</span>
            <span className="value">{detail.conviction_classified_14d ?? '—'}</span>
          </div>
        </>
      ) : (
        <div className="panel-row">
          <span className="label">Posts analysed</span>
          <span className="value">—</span>
        </div>
      )}

      {s.flags.length > 0 && (
        <>
          <h4>Warnings</h4>
          <div className="flag-list">
            {s.flags.map((f) => (
              <span key={f} className="flag-chip" title={FLAG_LABELS[f] ?? f}>
                {FLAG_LABELS[f] ?? f}
              </span>
            ))}
          </div>
        </>
      )}
    </aside>
  )
}
