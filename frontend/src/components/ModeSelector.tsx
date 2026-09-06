import { Card } from './Card'
import { COOLING_MODES, MODE_LABEL, type Mode } from '../types'

/**
 * Cooling speeds only. Whether a stage cools or warms is worked out from its
 * temperature, so warming is not a thing to pick here.
 */
export function ModeSelector({
  mode,
  onChange,
  disabled,
  note,
}: {
  mode: Mode
  onChange: (mode: Mode) => void
  disabled?: boolean
  note?: string | null
}) {
  return (
    <Card label="Cooling speed" chevron={false}>
      <div className="segmented" role="group" aria-label="Cooling mode">
        {COOLING_MODES.map((m) => (
          <button
            key={m}
            type="button"
            className="segment"
            aria-pressed={mode === m}
            disabled={disabled}
            onClick={() => onChange(m)}
          >
            {MODE_LABEL[m]}
          </button>
        ))}
      </div>
      {note && <p className="footnote" style={{ marginTop: 12 }}>{note}</p>}
    </Card>
  )
}
