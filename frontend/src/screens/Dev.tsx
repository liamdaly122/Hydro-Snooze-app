import { useCallback, useEffect, useRef, useState } from 'react'
import { Card } from '../components/Card'
import { sim, type SimSnapshot } from '../api/http'

/**
 * The time machine, and a window into the simulated unit.
 *
 * Only reachable when the service reports a fake transmitter, so this cannot
 * appear on the Pi with real hardware attached. Jumping the clock on a unit that
 * is actually running would be a genuinely bad idea.
 *
 * The press log is the point of the whole exercise: every press, what the unit
 * did about it, and which ones were swallowed.
 */
export function Dev() {
  const [snap, setSnap] = useState<SimSnapshot | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const logRef = useRef<HTMLPreElement>(null)

  const refresh = useCallback(async () => {
    try {
      setSnap(await sim.get())
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    void refresh()
    const timer = setInterval(() => void refresh(), 1000)
    return () => clearInterval(timer)
  }, [refresh])

  useEffect(() => {
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [snap?.press_log.length])

  async function act(fn: () => Promise<SimSnapshot>) {
    setBusy(true)
    try {
      setSnap(await fn())
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const unit = snap?.unit

  return (
    <>
      <Card label="Simulated clock" chevron={false}>
        <div className="dev__clock">{snap ? snap.now.slice(11, 19) : '--:--:--'}</div>
        <div className="dev__date">{snap ? formatDate(snap.now) : ''}</div>

        <div className="dev__row">
          <button className="dev__btn" disabled={busy} onClick={() => act(() => sim.clock({ jump_to: '21:29' }))}>
            Jump to 21:29
          </button>
          <button className="dev__btn" disabled={busy} onClick={() => act(() => sim.clock({ jump_to: '21:59' }))}>
            Jump to 21:59
          </button>
          <button className="dev__btn" disabled={busy} onClick={() => act(() => sim.clock({ advance_minutes: 60 }))}>
            +1 hour
          </button>
        </div>

        <p className="footnote" style={{ margin: '14px 4px 6px' }}>
          21:29 is a minute before pre-cooling starts, 21:59 a minute before arming. Speed up time
          to watch either without waiting.
        </p>

        <div className="dev__row">
          {[1, 10, 60, 120].map((speed) => (
            <button
              key={speed}
              className="dev__btn"
              aria-pressed={snap?.speed === speed}
              disabled={busy}
              onClick={() => act(() => sim.clock({ speed }))}
            >
              {speed}x
            </button>
          ))}
        </div>
      </Card>

      <Card label="Simulated unit" chevron={false}>
        {unit ? (
          <dl className="dev__facts">
            <Fact k="Power" v={unit.powered ? 'On' : 'Off'} />
            <Fact k="Mode" v={unit.mode} />
            <Fact k="Target" v={unit.target_c === null ? '--' : `${unit.target_c}°C`} />
            <Fact k="Display" v={unit.display_awake ? 'Awake' : 'Dark'} />
            <Fact k="Schedule" v={unit.schedule_running ? 'Running' : 'Not running'} />
            <Fact k="Ends" v={unit.schedule_ends_at ? unit.schedule_ends_at.slice(11, 16) : '--'} />
            <Fact k="Phases" v={unit.phase_temps.map((t) => `${t}°`).join(' ')} />
            <Fact k="Draw" v={`${unit.watts} W`} />
          </dl>
        ) : (
          <p className="empty">Not connected.</p>
        )}
        <div className="dev__row" style={{ marginTop: 16 }}>
          <button className="dev__btn" disabled={busy} onClick={() => act(sim.reset)}>
            Reset the unit
          </button>
        </div>
      </Card>

      <Card label="Press log" chevron={false}>
        <pre className="dev__log" ref={logRef}>
          {snap?.press_log.length ? snap.press_log.join('\n') : 'Nothing sent yet.'}
        </pre>
      </Card>

      {error && <p className="footnote">{error}</p>}
    </>
  )
}

function Fact({ k, v }: { k: string; v: string }) {
  return (
    <div className="dev__fact">
      <dt>{k}</dt>
      <dd>{v}</dd>
    </div>
  )
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-GB', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
  })
}
