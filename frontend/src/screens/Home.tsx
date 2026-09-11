import { useEffect, useState } from 'react'
import { TemperatureCard } from '../components/TemperatureCard'
import { WakeCard } from '../components/WakeCard'
import { ModeSelector } from '../components/ModeSelector'
import { StatusStrip } from '../components/StatusStrip'
import { AutopilotTeaser } from '../components/AutopilotTeaser'
import type { ApiClient } from '../api/client'
import type { DeviceState, Mode, Schedule, Stage } from '../types'

interface Props {
  client: ApiClient
  state: DeviceState
  schedule: Schedule
  maxC: number
  onOpenSchedule: () => void
  onOpenProfiles: () => void
  onOpenAutopilot: () => void
}

/**
 * There is no Save button any more.
 *
 * The unit used to hold the schedule, so changing it meant a forty-five second
 * infrared ritual walking its setup wizard, which nothing could verify. The
 * service drives every stage itself now, so a change is just a change: it saves,
 * and the next stage boundary uses it.
 */
export function Home({
  client,
  state,
  schedule,
  maxC,
  onOpenSchedule,
  onOpenProfiles,
  onOpenAutopilot,
}: Props) {
  const [draft, setDraft] = useState<Schedule>(schedule)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => setDraft(schedule), [schedule])

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
      {/*
        Top of the screen, above the controls, because in the morning it is the
        only thing anyone opens this for. It collapses to nothing on a morning
        with no finished night behind it rather than showing an empty shell.
      */}
      <AutopilotTeaser client={client} onOpen={onOpenAutopilot} />

      <TemperatureCard
        state={state}
        draft={draft}
        maxC={maxC}
        onOpenProfiles={onOpenProfiles}
        onStageChange={setStageTemp}
        onSetNow={(targetC) => {
          void client.setTemperature(targetC).catch((e: Error) => setError(e.message))
        }}
      />

      <WakeCard draft={draft} onDraftChange={save} onOpen={onOpenSchedule} />

      <ModeSelector
        state={state}
        scheduleMode={draft.cooling_speed}
        onScheduleChange={(cooling_speed: Mode) => save({ cooling_speed })}
        onLiveChange={(mode: Mode) => {
          void client.setMode(mode).catch((e: Error) => setError(e.message))
        }}
      />

      <StatusStrip
        state={state}
        onMute={() => {
          void client.mute().catch((e: Error) => setError(e.message))
        }}
        onRestartBlaster={() => {
          void client.restartBlaster().catch((e: Error) => setError(e.message))
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
