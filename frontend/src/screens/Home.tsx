import { useEffect, useState } from 'react'
import { TemperatureCard } from '../components/TemperatureCard'
import { TonightBanner } from '../components/TonightBanner'
import { HolidayBanner } from '../components/HolidayBanner'
import { KeepTonight, NudgeControls } from '../components/TonightControls'
import type { ApiClient } from '../api/client'
import type { DeviceState, Holiday, Schedule, Stage, TonightState } from '../types'

interface Props {
  client: ApiClient
  state: DeviceState
  schedule: Schedule
  /** Shown, never changed, from here. It is set from the menu. */
  holiday: Holiday | null
  tonight: TonightState | null
  onTonight: (tonight: TonightState) => void
  /** Ticks on the minute, so a nudge can count itself down. */
  now: Date
  maxC: number
  onOpenProfiles: () => void
}

/**
 * The temperature, and nothing else that is always there.
 *
 * Alarm, cooling speed, status and Autopilot each moved to a screen of their own
 * behind the side menu, along with the device chips. What stays is the card
 * you actually reach for at night, and the two lines that only appear when
 * tonight is not your usual night, because one of them is the undo for what
 * this card changes.
 */
export function Home({
  client,
  state,
  schedule,
  holiday,
  tonight,
  onTonight,
  now,
  maxC,
  onOpenProfiles,
}: Props) {
  const [draft, setDraft] = useState<Schedule>(schedule)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => setDraft(schedule), [schedule])

  function run(work: Promise<TonightState>) {
    setError(null)
    void work.then(onTonight).catch((e: Error) => setError(e.message))
  }

  function setStageTemp(stage: Stage, tempC: number) {
    // Tonight only, which is what the note under this card has said since the
    // tonight controls went in. It called putSchedule, so tapping + on Deep at
    // two in the morning rewrote the routine for every night after it, and the
    // app said the opposite while doing it. The permanent change is the Save as
    // my usual pill inside this card, and that is the only thing that should be.
    run(client.setStageTonight(stage, tempC))
  }

  return (
    <>
      <HolidayBanner holiday={holiday} now={now} />

      {/*
        Absent unless tonight is not your usual night. It is the only
        glance-level answer to "is anything different", and the only undo
        anyone needs.
      */}
      {tonight && (
        <TonightBanner
          tonight={tonight}
          usual={schedule}
          onClear={() => run(client.clearTonight())}
        />
      )}

      <TemperatureCard
        state={state}
        draft={tonight?.running ?? draft}
        maxC={maxC}
        onOpenProfiles={onOpenProfiles}
        onStageChange={setStageTemp}
        keep={
          tonight && (
            <KeepTonight
              tonight={tonight}
              usualStages={schedule.stages}
              onKeep={() => run(client.keepTonight().then(() => client.getTonight()))}
            />
          )
        }
        onSetNow={(targetC) => run(client.setTemperature(targetC).then(() => client.getTonight()))}
      >
        {/*
          Inside the card rather than under it, so the nudge sits with the
          temperature it nudges. Only while a night is running: "1 degree cooler
          for half an hour" means nothing to a unit that is switched off.
        */}
        {tonight?.phase === 'running' && !tonight.skip && (
          <NudgeControls
            tonight={tonight}
            now={now}
            onNudge={(delta) => run(client.nudgeTonight(delta))}
            onCancel={() => run(client.nudgeTonight(0))}
          />
        )}
      </TemperatureCard>

      {error && (
        <p className="footnote footnote--error" onClick={() => setError(null)}>
          {error}
        </p>
      )}
    </>
  )
}
