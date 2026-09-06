import { useEffect, useState } from 'react'
import { TemperatureCard } from '../components/TemperatureCard'
import { WakeCard } from '../components/WakeCard'
import { ModeSelector } from '../components/ModeSelector'
import { StatusStrip } from '../components/StatusStrip'
import type { ApiClient } from '../api/client'
import { WARMING_FLOOR_C, type DeviceState, type Mode, type Schedule, type Stage } from '../types'

interface Props {
  client: ApiClient
  state: DeviceState
  schedule: Schedule
  maxC: number
}

/**
 * There is no Save button any more.
 *
 * The unit used to hold the schedule, so changing it meant a forty-five second
 * infrared ritual walking its setup wizard, which nothing could verify. The
 * service drives every stage itself now, so a change is just a change: it saves,
 * and the next stage boundary uses it.
 */
export function Home({ client, state, schedule, maxC }: Props) {
  const [draft, setDraft] = useState<Schedule>(schedule)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => setDraft(schedule), [schedule])

  function save(patch: Partial<Schedule>) {
    setDraft((prev) => ({ ...prev, ...patch }))
    void client.putSchedule(patch).catch((e: Error) => setError(e.message))
  }

  function setStageTemp(stage: Stage, tempC: number) {
    const stages = draft.stages.map((s) => (s.stage === stage ? { ...s, temp_c: tempC } : s))
    const patch: Partial<Schedule> = { stages }

    // Dropping the first stage below warming's floor makes pre-heating
    // impossible, so the setting follows rather than leaving a combination the
    // service will refuse.
    if (
      stage === draft.stages[0]?.stage &&
      draft.precondition === 'warm' &&
      tempC < WARMING_FLOOR_C
    ) {
      patch.precondition = 'cool'
      setError(
        `Pre-heating switched off: warming cannot reach ${tempC}°C, its lowest setting is ` +
          `${WARMING_FLOOR_C}°C.`,
      )
    }
    save(patch)
  }

  function setStageDuration(stage: Stage, minutes: number) {
    save({
      stages: draft.stages.map((s) =>
        s.stage === stage ? { ...s, duration_minutes: Math.max(15, minutes) } : s,
      ),
    })
  }

  return (
    <>
      <TemperatureCard
        state={state}
        draft={draft}
        maxC={maxC}
        onStageChange={setStageTemp}
        onSetNow={(targetC) => {
          void client.setTemperature(targetC).catch((e: Error) => setError(e.message))
        }}
      />

      <WakeCard draft={draft} onDraftChange={save} onStageDuration={setStageDuration} />

      <ModeSelector
        mode={draft.cooling_speed}
        onChange={(cooling_speed: Mode) => save({ cooling_speed })}
        note="How hard the unit works when a stage is cooling. Quiet is the slowest and the least noisy, which matters next to a bed. Warming stages ignore this."
      />

      <StatusStrip state={state} />

      {error && (
        <p className="footnote" onClick={() => setError(null)}>
          {error}
        </p>
      )}

      <p className="footnote">
        The app drives each part of the night itself, rather than handing a schedule to the unit.
        That is what allows cooling and heating in the same night. It also means the Pi has to stay
        running: if it stops, the bed stays wherever it was and will not switch itself off.
      </p>

      <p className="footnote">
        The wake time sets temperature only. It is not an alarm and cannot wake you. Keep your
        actual alarm in the Clock app.
      </p>
    </>
  )
}
