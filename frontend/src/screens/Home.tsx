import { useEffect, useState } from 'react'
import { TemperatureCard } from '../components/TemperatureCard'
import { WakeCard } from '../components/WakeCard'
import { ModeSelector } from '../components/ModeSelector'
import { StatusStrip } from '../components/StatusStrip'
import { AutopilotTeaser } from '../components/AutopilotTeaser'
import { TonightBanner } from '../components/TonightBanner'
import { KeepTonight, NudgeControls, ShiftControls } from '../components/TonightControls'
import type { ApiClient } from '../api/client'
import type { DeviceState, Mode, Schedule, Stage, TonightState } from '../types'

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

  // Tonight lives here rather than in each card, because three of them need it
  // and two of them need to agree about it. `now` ticks only so the nudge can
  // count itself down; nothing else on this screen cares what minute it is.
  const [tonight, setTonight] = useState<TonightState | null>(null)
  const [now, setNow] = useState(() => new Date())

  useEffect(() => setDraft(schedule), [schedule])

  useEffect(() => {
    let live = true
    const load = () =>
      void client
        .getTonight()
        .then((t) => live && setTonight(t))
        .catch(() => undefined)
    load()
    // Re-read on the minute: a phase changes at bedtime and a nudge lapses on
    // its own, and neither sends anything to say so.
    const tick = setInterval(() => {
      if (!live) return
      setNow(new Date())
      load()
    }, 60_000)
    return () => {
      live = false
      clearInterval(tick)
    }
  }, [client, schedule])

  function onTonight(work: Promise<TonightState>) {
    setError(null)
    void work.then(setTonight).catch((e: Error) => setError(e.message))
  }

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

      {/*
        Above everything, and absent unless tonight is not your usual night.
        It is the only glance-level answer to "is anything different", and the
        only undo anyone needs.
      */}
      {tonight && (
        <TonightBanner
          tonight={tonight}
          usual={schedule}
          onClear={() => onTonight(client.clearTonight())}
        />
      )}

      <TemperatureCard
        state={state}
        draft={tonight?.running ?? draft}
        maxC={maxC}
        onOpenProfiles={onOpenProfiles}
        onStageChange={setStageTemp}
        onSetNow={(targetC) => {
          void client
            .setTemperature(targetC)
            .then(() => client.getTonight().then(setTonight))
            .catch((e: Error) => setError(e.message))
        }}
      >
        {/*
          Inside the card rather than under it, so the nudge sits with the
          temperature it nudges. Only while a night is running: "1 degree cooler
          for half an hour" means nothing to a unit that is switched off.
        */}
        {tonight?.phase === 'running' && (
          <NudgeControls
            tonight={tonight}
            now={now}
            onNudge={(delta) => onTonight(client.nudgeTonight(delta))}
            onCancel={() => onTonight(client.nudgeTonight(0))}
          />
        )}
        {tonight && (
          <KeepTonight
            tonight={tonight}
            usualStages={schedule.stages}
            onKeep={() => {
              setError(null)
              void client
                .keepTonight()
                .then(() => client.getTonight().then(setTonight))
                .catch((e: Error) => setError(e.message))
            }}
          />
        )}
      </TemperatureCard>

      <WakeCard draft={tonight?.running ?? draft} usual={schedule} onDraftChange={save} onOpen={onOpenSchedule}>
        {/*
          Shaping the night, which is something you do before you are in it.
          "Bed early" goes once the bed is already getting ready; "sleep in"
          stays, because at three in the morning a lie-in is still a thing you
          might want.
        */}
        {tonight && (tonight.phase === 'evening' || tonight.phase === 'running') && (
          <ShiftControls
            bedTime={tonight.running.bed_time}
            wakeTime={tonight.running.wake_time}
            showBedEarly={tonight.phase === 'evening'}
            onShift={(patch) => onTonight(client.shiftTonight(patch))}
          />
        )}
      </WakeCard>

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
