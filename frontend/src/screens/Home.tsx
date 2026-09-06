import { useEffect, useState } from 'react'
import { TemperatureCard } from '../components/TemperatureCard'
import { WakeCard } from '../components/WakeCard'
import { ModeSelector } from '../components/ModeSelector'
import { StatusStrip } from '../components/StatusStrip'
import { SaveSheet, type SaveStage } from '../components/SaveSheet'
import type { ApiClient } from '../api/client'
import type { DeviceState, Mode, Schedule, WriteProgress } from '../types'

/**
 * Fields the unit itself has to be told about. Everything else, wake time and days
 * and the pre-cool lead, lives only in the service and saves quietly, because the
 * unit has no clock and does not care what time it is.
 */
const UNIT_FIELDS = ['phase1_temp_c', 'phase2_temp_c', 'phase3_temp_c', 'mode'] as const

function needsUnitWrite(draft: Schedule, saved: Schedule): boolean {
  return UNIT_FIELDS.some((f) => draft[f] !== saved[f]) || saved.last_written_at === null
}

interface Props {
  client: ApiClient
  state: DeviceState
  schedule: Schedule
  maxC: number
}

export function Home({ client, state, schedule, maxC }: Props) {
  const [draft, setDraft] = useState<Schedule>(schedule)
  const [stage, setStage] = useState<SaveStage | null>(null)
  const [progress, setProgress] = useState<WriteProgress | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Adopt anything the service pushes, unless it would stamp on an edit in flight.
  useEffect(() => {
    setDraft((prev) => (needsUnitWrite(prev, schedule) ? { ...schedule, ...pickUnitFields(prev) } : schedule))
  }, [schedule])

  /** Phase temperatures and mode: held locally until written to the unit. */
  function editDraft(patch: Partial<Schedule>) {
    setDraft((prev) => ({ ...prev, ...patch }))
  }

  /** Timing settings: saved straight away, since nothing needs sending anywhere. */
  function saveNow(patch: Partial<Schedule>) {
    setDraft((prev) => ({ ...prev, ...patch }))
    void client.putSchedule(patch).catch((e: Error) => setError(e.message))
  }

  async function write() {
    setStage('running')
    setProgress(null)
    try {
      await client.putSchedule(pickUnitFields(draft))
      await client.writeSchedule(setProgress)
      setStage('done')
    } catch (e) {
      setError((e as Error).message)
      setStage('failed')
    }
  }

  const dirty = needsUnitWrite(draft, schedule)

  return (
    <>
      <TemperatureCard
        state={state}
        draft={draft}
        maxC={maxC}
        onDraftChange={editDraft}
        onSetNow={(targetC) => {
          void client.setTemperature(targetC).catch((e: Error) => setError(e.message))
        }}
      />

      {dirty && (
        <div className="banner">
          <span className="banner__text">
            {schedule.last_written_at === null
              ? 'These temperatures have never been sent to the unit.'
              : 'Phase temperatures changed. The unit still has the old ones.'}
          </span>
          <button type="button" className="banner__action" onClick={() => setStage('confirm')}>
            Save
          </button>
        </div>
      )}

      <WakeCard draft={draft} onDraftChange={saveNow} />

      <ModeSelector
        mode={draft.mode}
        onChange={(mode: Mode) => editDraft({ mode })}
        note="Set this before the schedule arms. Once it is running the unit will not switch between cooling and warming."
      />

      <StatusStrip state={state} />

      <p className="footnote">
        The wake time sets the unit's temperature schedule, working backwards 8 hours 30 minutes to
        decide when to arm it. It is not an alarm and cannot wake you. Keep your actual alarm in the
        Clock app.
      </p>

      {stage && (
        <SaveSheet
          stage={stage}
          progress={progress}
          error={error}
          onConfirm={() => void write()}
          onClose={() => {
            setStage(null)
            setError(null)
          }}
        />
      )}
    </>
  )
}

function pickUnitFields(s: Schedule): Pick<Schedule, (typeof UNIT_FIELDS)[number]> {
  return {
    phase1_temp_c: s.phase1_temp_c,
    phase2_temp_c: s.phase2_temp_c,
    phase3_temp_c: s.phase3_temp_c,
    mode: s.mode,
  }
}
