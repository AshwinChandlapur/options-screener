import type { DailyBucket } from '../types/narrative'

interface SparklineProps {
  buckets: DailyBucket[]
  width?: number
  height?: number
  color?: string
}

/**
 * Inline SVG bar chart of daily mention counts over the last 14 days.
 *
 * Bars are better than a line for discrete daily integer counts — each day
 * is an independent bucket, not a continuous value.
 * Renders nothing if buckets is empty so callers can drop it in unconditionally.
 */
export function Sparkline({ buckets, width = 160, height = 36, color = '#4dabf7' }: SparklineProps) {
  if (buckets.length === 0) return null

  const sorted = [...buckets].sort((a, b) => a.day.localeCompare(b.day))
  const counts = sorted.map((b) => b.count)
  const max = Math.max(...counts, 1)
  const n = sorted.length
  const gap = 1.5
  const barW = Math.max(1, (width - gap * (n - 1)) / n)

  return (
    <svg width={width} height={height} role="img" aria-label="14-day mention count bar chart">
      {sorted.map((b, i) => {
        const barH = Math.max(1, (b.count / max) * height)
        const x = i * (barW + gap)
        const y = height - barH
        const isLast = i === n - 1
        return (
          <rect
            key={b.day}
            x={x.toFixed(1)}
            y={y.toFixed(1)}
            width={barW.toFixed(1)}
            height={barH.toFixed(1)}
            fill={isLast ? color : `${color}99`}
            rx={1}
          >
            <title>{b.day}: {b.count}</title>
          </rect>
        )
      })}
    </svg>
  )
}
