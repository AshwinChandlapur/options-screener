/**
 * Lifecycle stage badge — methodology §4.
 *
 * Stages 1–6 map to a fixed colour ladder so the eye can scan a long table
 * for the target window (stages 2–3) without reading numbers.
 */

const STAGE_META: Record<number, { label: string; description: string; color: string }> = {
  0: { label: '—', description: 'Detector has not run for this ticker yet', color: '#888' },
  1: { label: '1', description: 'Pre-narrative — isolated mentions, low persistence', color: '#7a7a7a' },
  2: { label: '2', description: 'Forming — recurring across subreddits (target window)', color: '#1f9d55' },
  3: { label: '3', description: 'Expanding awareness — contributor base growing (target window)', color: '#2ec27e' },
  4: { label: '4', description: 'Institutional attention — late, partial signal', color: '#f59f00' },
  5: { label: '5', description: 'Consensus — mainstream coverage', color: '#e8590c' },
  6: { label: '6', description: 'Exhaustion — momentum decay, exit window', color: '#c92a2a' },
}

interface StageBadgeProps {
  stage: number
  confidence?: number
}

export function StageBadge({ stage, confidence }: StageBadgeProps) {
  const meta = STAGE_META[stage] ?? STAGE_META[0]
  const conf = confidence == null ? '' : ` · conf ${(confidence * 100).toFixed(0)}%`
  return (
    <span
      className="stage-badge"
      style={{ backgroundColor: meta.color }}
      title={`Stage ${meta.label}: ${meta.description}${conf}`}
    >
      {meta.label}
    </span>
  )
}
