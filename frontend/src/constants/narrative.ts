/**
 * Plain-English labels for the compound conviction signal
 * (direction × substance) emitted by the scorer.
 *
 * ADR-0021 retired the legacy 10-state taxonomy in favour of the four
 * axis combinations below.
 */
export const SIGNAL_LABELS: Record<string, string> = {
  bull_researched: 'Bullish — analytical',
  bear_researched: 'Bearish — analytical',
  bull_emotional:  'Bullish — hype-driven',
  bear_emotional:  'Bearish — hype-driven',
  // Sentiment-only fallback when the classifier has not run yet.
  bullish:         'Bullish (sentiment only)',
  bearish:         'Bearish (sentiment only)',
  unknown:         '—',
}

/** Translate a dominant_signal label to display text. */
export function labelSignal(state: string | null | undefined): string {
  if (!state) return '—'
  return SIGNAL_LABELS[state] ?? state
}
