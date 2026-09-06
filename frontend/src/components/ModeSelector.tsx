import { Card } from './Card'
import { ALL_MODES, MODE_LABEL, type Mode } from '../types'

/**
 * Warming is offered but sits apart in meaning: it cannot be switched into once a
 * schedule is running, so getting it right before arming is the whole point.
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
    <Card label="Mode" chevron={false}>
      <div className="segmented" role="group" aria-label="Cooling mode">
        {ALL_MODES.map((m) => (
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
