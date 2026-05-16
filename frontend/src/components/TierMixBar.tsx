interface TierMixBarProps {
  tier1: number  // 0..1
  tier2: number
  tier3: number
  width?: number
  height?: number
}

/**
 * Stacked horizontal bar showing contributor tier mix.
 *
 * Tier 1 = high-quality researched; Tier 2 = medium; Tier 3 = low/emotional.
 */
export function TierMixBar({ tier1, tier2, tier3, width = 160, height = 10 }: TierMixBarProps) {
  const total = tier1 + tier2 + tier3
  if (total <= 0) {
    return <span style={{ opacity: 0.4, fontSize: '0.9em' }} title="Tier data not yet available">—</span>
  }
  const w1 = (tier1 / total) * width
  const w2 = (tier2 / total) * width
  const w3 = (tier3 / total) * width
  const fmt = (v: number) => `${(v * 100).toFixed(0)}%`
  return (
    <div
      className="tier-mix-bar"
      style={{ width, height, display: 'flex', borderRadius: 2, overflow: 'hidden' }}
      title={`Tier 1 ${fmt(tier1)} · Tier 2 ${fmt(tier2)} · Tier 3 ${fmt(tier3)}`}
    >
      <span style={{ width: w1, background: '#2ec27e' }} />
      <span style={{ width: w2, background: '#f59f00' }} />
      <span style={{ width: w3, background: '#868e96' }} />
    </div>
  )
}
