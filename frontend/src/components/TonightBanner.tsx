import { STAGE_LABEL } from '../types'
import type { Schedule, TonightState } from '../types'

/**
 * One slim line, only when tonight is not your usual night.
 *
 * It is the only way to see at a glance that something is different, and the
 * only undo you need. Absent the rest of the time, which is most of the time:
 * a banner that is always there is a banner nobody reads.
 *
 * It names what changed rather than saying "tonight is different", because the
 * question anyone asks next is "different how", and answering it here saves a
 * tap and a hunt through three cards.
 */
export function TonightBanner({
  tonight,
  usual,
  onClear,
}: {
  tonight: TonightState
  usual: Schedule
  onClear: () => void
}) {
  if (!tonight.changed) return null

  // Autopilot's evening suggestion, as it was taken. Named as Autopilot's, with
  // the usual beside each moved part, so a test night reads as a test and not
  // as something somebody set and forgot.
  const suggested = tonight.suggested ?? null
  const parts: string[] = []

  if (tonight.skip) {
    parts.push('not running')
  } else {
    // Only the stages that actually moved, named. "Deep 17°" beats "temperatures
    // changed" by exactly the amount of thinking it saves.
    for (const stage of tonight.running.stages) {
      const before = usual.stages.find((s) => s.stage === stage.stage)
      if (before && before.temp_c !== stage.temp_c) {
        parts.push(
          suggested
            ? `${STAGE_LABEL[stage.stage]} ${stage.temp_c}°, usual ${before.temp_c}°`
            : `${STAGE_LABEL[stage.stage]} ${stage.temp_c}°`,
        )
      }
    }
    // Against this night's own times, which on a weekend are the weekend's.
    if (tonight.running.wake_time !== (tonight.usual_wake_time ?? usual.wake_time)) {
      parts.push(`alarm ${formatHHMM(tonight.running.wake_time)}`)
    }
    if (tonight.running.bed_time !== (tonight.usual_bed_time ?? usual.bed_time)) {
      parts.push(`bed ${formatHHMM(tonight.running.bed_time)}`)
    }
    // Set from the Cooling speed screen and just as much tonight only. Left
    // out, a Turbo evening read as "Tonight only · changed", and changed how
    // was a trip through the menu to find out.
    if (tonight.running.cooling_speed !== usual.cooling_speed) {
      const speed = tonight.running.cooling_speed
      parts.push(`${speed.charAt(0).toUpperCase()}${speed.slice(1)} speed`)
    }
  }

  return (
    <div className="tonight">
      <span className="tonight__dot" />
      <span className="tonight__text">
        <span className="tonight__title">
          {suggested
            ? suggested.test
              ? 'Autopilot test tonight'
              : "Autopilot's suggestion tonight"
            : 'Tonight only'}
        </span>
        <span className="tonight__sub">{parts.join(' · ') || 'changed'}</span>
      </span>
      <button type="button" className="tonight__undo" onClick={onClear}>
        Back to usual
      </button>
    </div>
  )
}

/** The schedule carries "HH:MM" already; this is here so the intent is obvious. */
function formatHHMM(value: string): string {
  return value.slice(0, 5)
}
