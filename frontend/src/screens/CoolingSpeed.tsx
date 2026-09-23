import { useState } from 'react'
import { ModeSelector } from '../components/ModeSelector'
import type { ApiClient } from '../api/client'
import type { DeviceState, Mode, Schedule, TonightState } from '../types'

interface Props {
  client: ApiClient
  state: DeviceState
  schedule: Schedule
  tonight: TonightState | null
  onTonight: (tonight: TonightState) => void
}

/** The Cooling speed card, on a screen of its own. Moved, not redesigned. */
export function CoolingSpeed({ client, state, schedule, tonight, onTonight }: Props) {
  const [error, setError] = useState<string | null>(null)

  function run(work: Promise<TonightState>) {
    setError(null)
    void work.then(onTonight).catch((e: Error) => setError(e.message))
  }

  return (
    <>
      <ModeSelector
        state={state}
        // Tonight's, as the tab says. It used to save the routine, so one warm
        // evening on Turbo became every night on Turbo.
        scheduleMode={tonight?.running.cooling_speed ?? schedule.cooling_speed}
        usualMode={schedule.cooling_speed}
        onScheduleChange={(cooling_speed: Mode) => run(client.speedTonight(cooling_speed))}
        // The deliberate one, as with the temperatures. The usual speed first,
        // then tonight's taken back off, because it now matches.
        onKeep={(cooling_speed: Mode) =>
          run(client.putSchedule({ cooling_speed }).then(() => client.speedTonight(cooling_speed)))
        }
        onLiveChange={(mode: Mode) => {
          setError(null)
          void client.setMode(mode).catch((e: Error) => setError(e.message))
        }}
      />

      {error && (
        <p className="footnote footnote--error" onClick={() => setError(null)}>
          {error}
        </p>
      )}
    </>
  )
}
