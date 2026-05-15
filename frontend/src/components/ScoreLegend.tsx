/**
 * Score legend — explains the A–E components and common flags.
 *
 * Keeps the table itself uncluttered. Links to the methodology doc for the
 * full derivation.
 */

const COMPONENTS = [
  ['A', 'Attention persistence', 'Mentions × distinct days × decay'],
  ['B', 'Contributor quality', 'Author tier mix (Tier 1/2/3) — methodology §5.4'],
  ['C', 'Narrative strength', 'Coherence across posts; thesis convergence'],
  ['D', 'Thesis quality', 'Researched bull/bear ratios; DD intensity'],
  ['E', 'Market confirmation', 'Price/options confirmation of the narrative'],
] as const

const FLAGS = [
  ['gini_high', 'Single-author dominance (Gini > 0.5)'],
  ['small_cap_haircut', 'Capped by §5.3 small-cap adjustment'],
  ['decelerating_3d', '3-day mention deceleration — momentum cooling'],
  ['low_unique_authors', 'Fewer than the §5.6 minimum unique contributors'],
] as const

export function ScoreLegend() {
  return (
    <details className="score-legend">
      <summary>How is this scored?</summary>
      <div className="score-legend-body">
        <h4>Components (A–E)</h4>
        <ul>
          {COMPONENTS.map(([key, name, desc]) => (
            <li key={key}>
              <strong>{key}</strong> — {name}: <span style={{ opacity: 0.8 }}>{desc}</span>
            </li>
          ))}
        </ul>
        <h4>Flags</h4>
        <ul>
          {FLAGS.map(([flag, desc]) => (
            <li key={flag}>
              <code>{flag}</code> — {desc}
            </li>
          ))}
        </ul>
        <p style={{ opacity: 0.7, fontSize: '0.85em' }}>
          Full derivation:{' '}
          <a
            href="https://github.com/ashwincha/Options/blob/main/docs/NARRATIVE_METHODOLOGY.md"
            target="_blank"
            rel="noopener noreferrer"
          >
            NARRATIVE_METHODOLOGY.md
          </a>
        </p>
      </div>
    </details>
  )
}
