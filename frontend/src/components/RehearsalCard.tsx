import { useEffect, useState } from 'react'
import { Card } from './Card'
import { STAGE_LABEL, type DeviceState, type Schedule } from '../types'

interface Props {
  state: DeviceState
  schedule: Schedule
  onStart: (seconds: number) => Promise<void>
  onStop: () => Promise<void>
}

/** Seconds of stages. The warm-up before them adds about another minute, so
 *  the whole thing runs a little under six. */
const SECONDS = 300

function remaining(endsAt: string): number {
  return Math.max(0, Math.round((new Date(endsAt).getTime() - Date.now()) / 1000))
}

function mmss(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

/**
 * The whole night, compressed, on demand.
 *
 * Everything else in this project has been provable without hardware. This is
 * the one thing that cannot be: whether a stage boundary at two in the morning
 * really lands on a unit that is actually in the room. Reading the code cannot
 * answer it and neither can the simulator, because the part that has never run
 * is the infrared arriving somewhere real.
 *
 * So it runs the real thing on a short clock. Same scheduler, same sequences,
 * same plug checks, same order, tonight's own temperatures and modes. Only the
 * durations are generous.
 */
export function RehearsalCard({ state, schedule, onStart, onStop }: Props) {
  const running = state.rehearsal_ends_at
  const [left, setLeft] = useState(() => (running ? remaining(running) : 0))
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!running) return
    setLeft(remaining(running))
    const id = setInterval(() => setLeft(remaining(running)), 1000)
    return () => clearInterval(id)
  }, [running])

  async function press(action: () => Promise<void>) {
    setBusy(true)
    try {
      await action()
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card label="Test run">
      {running ? (
        <>
          <p className="rehearsal__now">
            Running. {mmss(left)} left
            {state.current_stage ? ` · ${STAGE_LABEL[state.current_stage]}` : ''}
          </p>
          <p className="footnote">
            Watch the unit's display change at each boundary, and the wattage on the History tab
            follow it. Every press is real.
          </p>
          <button
            type="button"
            className="rehearsal__btn rehearsal__btn--stop"
            disabled={busy}
            onClick={() => void press(onStop)}
          >
            Stop and switch off
          </button>
        </>
      ) : (
        <>
          <p className="footnote rehearsal__intro">
            Runs tonight's whole night in about six minutes, on the real unit. The same stages in
            the same order at the same temperatures, with the same checks. Only the clock is
            generous.
          </p>

          <ol className="rehearsal__steps">
            <li>
              <span className="rehearsal__step">Get ready</span>
              <span className="rehearsal__detail">
                {schedule.preconditioning.mode
                  ? `${schedule.preconditioning.mode} to ${schedule.stages[0]?.temp_c}°`
                  : 'nothing to do'}
              </span>
            </li>
            {schedule.stages.map((stage) => (
              <li key={stage.stage}>
                <span className="rehearsal__step">{STAGE_LABEL[stage.stage]}</span>
                <span className="rehearsal__detail">
                  {stage.temp_c}° in {stage.mode}
                </span>
              </li>
            ))}
            <li>
              <span className="rehearsal__step">Finish</span>
              <span className="rehearsal__detail">switch off</span>
            </li>
          </ol>

          <button
            type="button"
            className="rehearsal__btn"
            disabled={busy || schedule.stages.length === 0}
            onClick={() => void press(() => onStart(SECONDS))}
          >
            {busy ? 'Starting…' : 'Run a test night'}
          </button>
          <p className="footnote">
            It takes over from the real schedule while it runs and hands back afterwards, and it
            always ends by switching the unit off. Do it when you are not about to go to bed.
          </p>
        </>
      )}
    </Card>
  )
}
