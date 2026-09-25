/**
 * How many nights there are, against how many something needs.
 *
 * A bar rather than a row of dots, because fourteen dots on a phone is a
 * pattern to count and a bar is a thing to read. `marks` are the milestones on
 * the way, drawn as ticks, and the caption names the next one.
 */
export function NightsProgress({
  nights,
  needs,
  marks = [],
  label,
}: {
  nights: number
  needs: number
  marks?: { at: number; label: string }[]
  label: string
}) {
  const shown = Math.max(0, Math.min(nights, needs))
  const next = [...marks, { at: needs, label: `${label} at ${needs}` }]
    .filter((m) => m.at > shown)
    .sort((a, b) => a.at - b.at)[0]

  return (
    <div className="nights">
      <div className="nights__bar">
        <div
          className="progress__track"
          role="progressbar"
          aria-label={label}
          aria-valuemin={0}
          aria-valuemax={needs}
          aria-valuenow={shown}
          aria-valuetext={`${shown} of ${needs} nights`}
        >
          <div className="progress__fill" style={{ width: `${(shown / needs) * 100}%` }} />
        </div>
        {marks.map((m) => (
          <span
            key={m.at}
            className={`nights__mark${shown >= m.at ? ' nights__mark--passed' : ''}`}
            style={{ left: `${(m.at / needs) * 100}%` }}
            aria-hidden="true"
          />
        ))}
      </div>
      <div className="progress__caption nights__caption">
        <span>
          {shown} of {needs} nights
        </span>
        {next && <span>{next.label}</span>}
      </div>
    </div>
  )
}
