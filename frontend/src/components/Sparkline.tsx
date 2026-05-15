import type { DailyBucket } from '../types/narrative'

interface SparklineProps {
  buckets: DailyBucket[]
  width?: number
  height?: number
  color?: string
}

/**
 * Inline SVG sparkline of mention counts over the last 14 days.
 *
 * Renders nothing if buckets is empty so callers can drop it in unconditionally.
 */
export function Sparkline({ buckets, width = 160, height = 36, color = '#4dabf7' }: SparklineProps) {
  if (buckets.length === 0) return null

  const sorted = [...buckets].sort((a, b) => a.day.localeCompare(b.day))
  const counts = sorted.map((b) => b.count)
  const max = Math.max(...counts, 1)
  const stepX = sorted.length > 1 ? width / (sorted.length - 1) : 0

  const points = sorted
    .map((b, i) => {
      const x = i * stepX
      const y = height - (b.count / max) * height
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')

  const last = sorted[sorted.length - 1]
  const lastX = (sorted.length - 1) * stepX
  const lastY = height - (last.count / max) * height

  return (
    <svg width={width} height={height} role="img" aria-label="14-day mention count sparkline">
      <polyline fill="none" stroke={color} strokeWidth={1.5} points={points} />
      <circle cx={lastX} cy={lastY} r={2.5} fill={color} />
    </svg>
  )
}
