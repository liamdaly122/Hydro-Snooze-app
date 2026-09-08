import { useEffect, useState } from 'react'
import { TemperatureCard } from '../components/TemperatureCard'
import { WakeCard } from '../components/WakeCard'
import { ModeSelector } from '../components/ModeSelector'
import { StatusStrip } from '../components/StatusStrip'
import type { ApiClient } from '../api/client'
import type { DeviceState, Mode, Schedule, Stage } from '../types'

interface Props {
  client: ApiClient
  state: DeviceState
  schedule: Schedule
  maxC: number
  onOpenSchedule: () => void
}

/**
 * There is no Save button any more.
 *
 * The unit used to hold the schedule, so changing it meant a forty-five second
 * infrared ritual walking its setup wizard, which nothing could verify. The
 * service drives every stage itself now, so a change is just a change: it saves,
 * and the next stage boundary uses it.
 */
export function Home({ client, state, schedule, maxC, onOpenSchedule }: Props) {
  const [draft, setDraft] = useState<Schedule>(schedule)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => setDraft(schedule), [schedule])

  // Whether a change here should also reach the unit right now. The mode has to
  // be a cooling one and it has to be one we actually know: `assumed_mode` is
  // null whenever the app has lost track, and acting on a guess is the one thing
  // this project does not do.
  const coolingNow =
    state.power === 'on' && state.assumed_mode !== null && state.assumed_mode !== 'warming'
  const appliesNow = coolingNow
  const whyNotNow =
    state.power !== 'on'
      ? 'The unit is off, so nothing is sent now.'
      : state.assumed_mode === 'warming'
        ? 'The unit is warming right now, so nothing is sent: that would start cooling the bed.'
        : 'The unit\'s mode is unknown, so nothing is sent rather than guessing.'

  function save(patch: Partial<Schedule>) {
    setDraft((prev) => ({ ...prev, ...patch }))
    void client.putSchedule(patch).catch((e: Error) => setError(e.message))
  }

  function setStageTemp(stage: Stage, tempC: number) {
    // Pre-conditioning used to have to be kept in step with this by hand. It is
    // derived from the first stage now, so changing a temperature is just that.
    save({ stages: draft.stages.map((s) => (s.stage === stage ? { ...s, temp_c: tempC } : s)) })
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

      <WakeCard draft={draft} onDraftChange={save} onOpen={onOpenSchedule} />

      {/*
        This used to only save the setting, so on a running unit the buttons
        highlighted and nothing happened, which reads as broken.

        It is genuinely a schedule setting, used by every cooling stage tonight.
        But when the unit is cooling right now, changing the speed should change
        it now as well. Only when it is actually cooling: sending a cooling speed
        to a unit that is warming would switch it to cooling and start chilling a
        bed that was meant to be warm, and sending it when the mode is unknown
        would be guessing.
      */}
      <ModeSelector
        mode={draft.cooling_speed}
        onChange={(cooling_speed: Mode) => {
          save({ cooling_speed })
          if (appliesNow) {
            void client.setMode(cooling_speed).catch((e: Error) => setError(e.message))
          }
        }}
        note={
          appliesNow
            ? 'Changed on the unit now, and used by every cooling stage tonight. Quiet is the slowest and the least noisy, which matters next to a bed.'
            : `Used by every cooling stage tonight. ${whyNotNow} Warming stages ignore this.`
        }
      />

      <StatusStrip
        state={state}
        onMute={() => {
          void client.mute().catch((e: Error) => setError(e.message))
        }}
      />

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

    </>
  )
}
