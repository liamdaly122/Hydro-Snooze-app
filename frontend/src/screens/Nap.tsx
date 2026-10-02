import { useEffect, useRef, useState } from 'react'
import { Card } from '../components/Card'
import { Minus, Plus } from '../components/Icons'
import { formatDuration, tint } from '../domain'
import type { ApiClient } from '../api/client'
import { MODE_RANGE, type DeviceState, type NapPreview } from '../types'

interface Props {
  client: ApiClient
  state: DeviceState
  maxC: number
}

/** The lengths most naps are, one tap each. Anything else is the − and +. */
const QUICK = [20, 30, 45, 60, 90]
const SHORTEST = 5
const LONGEST = 180
const LENGTH_STEP = 5

/** How long the numbers have to stop moving before the preview is asked again. */
const SETTLE_MS = 250

function hhmm(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

/**
 * A nap: the bed to one temperature, held for a while once it gets there, then off.
 *
 * Behind the menu rather than on the home screen, because it is something you
 * set up and leave. Once it is running Home carries one line for it, with Stop.
 *
 * Before it starts, one line says what will happen in clock time: ready by
 * when, off by when. The ready time is how long this bed has measured it takes,
 * or the estimate until it has, and the length counts from then, not from the
 * press: see backend/hydrosnooze/nap.py.
 */
export function Nap({ client, state, maxC }: Props) {
  const nap = state.nap
  const [temp, setTemp] = useState<number | null>(null)
  const [minutes, setMinutes] = useState<number | null>(null)
  const [preview, setPreview] = useState<NapPreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const first = useRef(true)

  // The last nap's numbers to start from, then the preview again whenever they
  // settle on something new.
  useEffect(() => {
    let live = true
    const wait = first.current ? 0 : SETTLE_MS
    first.current = false
    const timer = setTimeout(() => {
      void client
        .getNapPreview(temp ?? undefined, minutes ?? undefined)
        .then((p) => {
          if (!live) return
          setPreview(p)
          setTemp((t) => t ?? p.temp_c)
          setMinutes((m) => m ?? p.minutes)
        })
        .catch(() => undefined)
    }, wait)
    return () => {
      live = false
      clearTimeout(timer)
    }
  }, [client, temp, minutes, nap === null])

  if (nap) return <Running nap={nap} onStop={() => client.stopNap()} />

  const floor = MODE_RANGE.quiet[0]
  const ceiling = Math.min(MODE_RANGE.warming[1], maxC)
  const shown = temp ?? preview?.temp_c ?? null
  const length = minutes ?? preview?.minutes ?? 30

  function start() {
    if (shown === null) return
    setBusy(true)
    setError(null)
    void client
      .startNap(shown, length)
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false))
  }

  return (
    <>
      <Card label="Nap">
        <div className="nap__temp">
          <button
            type="button"
            className="step"
            onClick={() => shown !== null && setTemp(Math.max(floor, shown - 1))}
            disabled={shown === null || shown <= floor}
            aria-label="Colder"
          >
            <Minus />
          </button>
          <span className="nap__value" style={shown === null ? undefined : { color: tint(shown) }}>
            {shown ?? '–'}
            <span className="stage__unit">°C</span>
          </span>
          <button
            type="button"
            className="step"
            onClick={() => shown !== null && setTemp(Math.min(ceiling, shown + 1))}
            disabled={shown === null || shown >= ceiling}
            aria-label="Warmer"
          >
            <Plus />
          </button>
        </div>

        <div className="segmented nap__quick" role="group" aria-label="How long">
          {QUICK.map((q) => (
            <button
              key={q}
              type="button"
              className="segment"
              aria-pressed={length === q}
              onClick={() => setMinutes(q)}
            >
              {q} min
            </button>
          ))}
        </div>

        <div className="bedtime-length__row nap__length">
          <button
            type="button"
            className="step step--small"
            onClick={() => setMinutes(Math.max(SHORTEST, length - LENGTH_STEP))}
            disabled={length <= SHORTEST}
            aria-label="Shorter"
          >
            <Minus />
          </button>
          <span className="bedtime-length__for">for {formatDuration(length)}</span>
          <button
            type="button"
            className="step step--small"
            onClick={() => setMinutes(Math.min(LONGEST, length + LENGTH_STEP))}
            disabled={length >= LONGEST}
            aria-label="Longer"
          >
            <Plus />
          </button>
        </div>

        {preview && <p className="footnote nap__preview">{saying(preview)}</p>}

        <button
          type="button"
          className="rehearsal__btn nap__start"
          disabled={busy || shown === null || Boolean(preview?.blocked)}
          onClick={start}
        >
          Start nap
        </button>
      </Card>

      {error && (
        <p className="footnote footnote--error" onClick={() => setError(null)}>
          {error}
        </p>
      )}

      <p className="footnote">
        The length counts from when the bed reaches the temperature, and you get a notification when
        it does. Then it switches off. The bedside on/off button stops it too. A nap is not a night,
        so it stays off the scoreboard and out of the morning report.
      </p>
    </>
  )
}

/** What will happen, in clock time, or why it cannot start. */
function saying(p: NapPreview): string {
  if (p.blocked) return p.blocked
  const way = p.mode === 'warming' ? 'Warms' : 'Cools'
  const ready =
    p.ready_in_minutes === 0
      ? 'Already there'
      : `${way} to it by about ${hhmm(p.ready_at)}${p.measured ? '' : ' (estimated)'}`
  const off = `off at ${hhmm(p.ends_at)}`
  const cut = p.cut_at
    ? ` Tonight starts getting ready at ${hhmm(p.cut_at)}, so the nap would end then.`
    : ''
  return `${ready}, ${off}.${cut}`
}

/** The nap under way: what it is doing, when it ends, and Stop. */
function Running({ nap, onStop }: { nap: NonNullable<DeviceState['nap']>; onStop: () => Promise<unknown> }) {
  const [busy, setBusy] = useState(false)
  const way = nap.mode === 'warming' ? 'Warming' : 'Cooling'
  return (
    <Card label="Nap">
      <div className="nap__temp nap__temp--running">
        <span className="nap__value" style={{ color: tint(nap.temp_c) }}>
          {nap.temp_c}
          <span className="stage__unit">°C</span>
        </span>
      </div>
      <p className="nap__status">
        {nap.ends_at
          ? `At ${nap.temp_c}° until ${hhmm(nap.ends_at)}, then off.`
          : `${way} to ${nap.temp_c}°, ready about ${hhmm(nap.expect_ready_at)}. ` +
            `The ${formatDuration(nap.minutes)} starts then.`}
      </p>
      <button
        type="button"
        className="rehearsal__btn nap__start"
        disabled={busy}
        onClick={() => {
          setBusy(true)
          void onStop().finally(() => setBusy(false))
        }}
      >
        Stop nap
      </button>
    </Card>
  )
}
