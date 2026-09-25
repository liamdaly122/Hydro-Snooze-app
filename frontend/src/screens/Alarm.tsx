import { useEffect, useState } from 'react'
import { WakeCard } from '../components/WakeCard'
import { ShiftControls } from '../components/TonightControls'
import type { ApiClient } from '../api/client'
import type { Holiday, Schedule, TonightState } from '../types'

interface Props {
  client: ApiClient
  schedule: Schedule
  tonight: TonightState | null
  holiday: Holiday | null
  onTonight: (tonight: TonightState) => void
  onOpenSchedule: () => void
}

/**
 * When it wakes me, whether it is on, and when the unit switches itself on.
 *
 * The Alarm card as it was on the home screen, moved rather than redesigned.
 * Its arrow still opens the whole schedule, and Back from there comes back
 * here rather than all the way home.
 */
export function Alarm({ client, schedule, tonight, holiday, onTonight, onOpenSchedule }: Props) {
  const [draft, setDraft] = useState<Schedule>(schedule)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => setDraft(schedule), [schedule])

  function save(patch: Partial<Schedule>) {
    setError(null)
    setDraft((prev) => ({ ...prev, ...patch }))
    void client.putSchedule(patch).catch((e: Error) => setError(e.message))
  }

  return (
    <>
      <WakeCard
        draft={tonight?.running ?? draft}
        usual={schedule}
        usualWake={tonight?.usual_wake_time}
        holiday={holiday}
        onDraftChange={save}
        onOpen={onOpenSchedule}
      >
        {/*
          Shaping the night, which is something you do before you are in it.
          "Bed early" goes once the bed is already getting ready; "sleep in"
          stays, because at three in the morning a lie-in is still a thing you
          might want. Neither on a skipped night, which has nothing to shape.
        */}
        {tonight && !tonight.skip && (tonight.phase === 'evening' || tonight.phase === 'running') && (
          <ShiftControls
            bedTime={tonight.running.bed_time}
            wakeTime={tonight.running.wake_time}
            showBedEarly={tonight.phase === 'evening'}
            onShift={(patch) => {
              setError(null)
              void client
                .shiftTonight(patch)
                .then(onTonight)
                .catch((e: Error) => setError(e.message))
            }}
          />
        )}
      </WakeCard>

      {error && (
        <p className="footnote footnote--error" onClick={() => setError(null)}>
          {error}
        </p>
      )}

      <p className="footnote">
        The arrow opens the whole schedule: lights out, the parts of the night, and which days it
        runs.
      </p>
    </>
  )
}
