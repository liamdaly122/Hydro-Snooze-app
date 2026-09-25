import { useEffect, useMemo, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { Card } from './Card'
import { Fold } from './Fold'
import { addDays, daysBetween, formatDay, parseDay } from '../domain'
import type { ApiClient } from '../api/client'
import type { TrendNight, TrendRange, TrendSummary, Trends as TrendsData } from '../types'

/**
 * How the nights are going over weeks and months, rather than one at a time.
 *
 * The Health Report is a night and a week strip; History, below this, is the
 * last day. Neither answers the question under all of it: is sleep getting
 * better, and what is it costing? The four figures at the top say that against
 * the same stretch before; the charts under them show how it got there.
 *
 * Small charts, one measure each, because every measure here is counted in
 * something different and one chart with two scales would invent a relationship
 * between them. Each draws the nights themselves as a faint line and a
 * seven-night average over it, which is the line to read: single nights swing by
 * more than anything the bed does. Gaps are gaps, the same rule as the bed chart
 * below.
 *
 * Touching any chart picks a night in all of them, and the line under the
 * figures reads it out. Every value is in the table at the bottom too, so none
 * of it depends on hovering.
 */

const RANGES: { days: TrendRange; label: string }[] = [
  { days: 30, label: '30 days' },
  { days: 90, label: '90 days' },
  { days: 365, label: 'A year' },
]

/** The seven-night average needs this many of the seven before it says anything. */
const AVERAGE_OVER = 7
const AVERAGE_NEEDS = 3

/**
 * The app's own blue and the bed's purple, from the bed chart. Checked against
 * the card's colour with a palette validator: both clear 3:1 contrast, and the
 * two stay well apart under every kind of colour blindness. The room is grey on
 * purpose, as context, and is dashed as well so it never relies on colour.
 */
const ACCENT = '#0a84ff'
const BED = '#a85bb4'
const ROOM = '#8e8e93'
/** The nights themselves: context under the average, so deliberately quiet. */
const NIGHTLY = '#48484a'

const W = 1000
const H = 110

function minutes(seconds: number): string {
  const m = Math.round(seconds / 60)
  return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m` : `${m}m`
}

function money(pence: number): string {
  return pence >= 100 ? `£${(pence / 100).toFixed(2)}` : `${Math.round(pence)}p`
}

type Measure = {
  key: string
  title: string
  get: (n: TrendNight) => number | null
  show: (v: number) => string
  /** The scale beside the chart, when `show` is too wide for its gutter. */
  tick?: (v: number) => string
  /** The least span the axis draws, so an ordinary spread does not look dramatic. */
  minSpan: number
  /** For a magnitude, the axis starts at nought. */
  fromZero?: boolean
  colour: string
}

const SCORE: Measure = {
  key: 'score',
  title: 'Sleep score',
  get: (n) => n.score,
  show: (v) => `${Math.round(v)}`,
  minSpan: 20,
  colour: ACCENT,
}

const DEEP_REM: Measure = {
  key: 'deep_rem',
  title: 'Deep and REM sleep',
  get: (n) => (n.deep_rem_s === null ? null : n.deep_rem_s / 60),
  show: (v) => minutes(v * 60),
  minSpan: 60,
  colour: ACCENT,
}

const LATENCY: Measure = {
  key: 'latency',
  title: 'Time to fall asleep',
  get: (n) => (n.latency_s === null ? null : n.latency_s / 60),
  show: (v) => minutes(v * 60),
  minSpan: 30,
  fromZero: true,
  colour: ACCENT,
}

const BED_C: Measure = {
  key: 'bed',
  title: 'Bed',
  get: (n) => n.bed_c,
  show: (v) => `${v.toFixed(1)}°`,
  minSpan: 6,
  colour: BED,
}

const ROOM_C: Measure = { ...BED_C, key: 'room', title: 'Room', get: (n) => n.room_c, colour: ROOM }

const ENERGY: Measure = {
  key: 'energy',
  title: 'Energy, kWh',
  get: (n) => n.kwh,
  show: (v) => `${v.toFixed(2)} kWh`,
  tick: (v) => v.toFixed(1),
  minSpan: 1,
  fromZero: true,
  colour: ACCENT,
}

/** The trailing seven-night average at each night, or null with too few behind it. */
function averaged(nights: TrendNight[], get: Measure['get']): (number | null)[] {
  return nights.map((n) => {
    const from = addDays(n.wake_on, -(AVERAGE_OVER - 1))
    const values = nights
      .filter((m) => m.wake_on >= from && m.wake_on <= n.wake_on)
      .map(get)
      .filter((v): v is number => v !== null)
    return values.length >= AVERAGE_NEEDS ? values.reduce((a, b) => a + b, 0) / values.length : null
  })
}

function axis(values: number[], m: Measure): [number, number] {
  let low = Math.min(...values)
  let high = Math.max(...values)
  if (m.fromZero) low = 0
  const short = m.minSpan - (high - low)
  if (short > 0) {
    if (m.fromZero) high += short
    else {
      low -= short / 2
      high += short / 2
    }
  }
  return [low, high]
}

function Chart({
  measures,
  nights,
  first,
  days,
  focus,
  onFocus,
}: {
  measures: Measure[]
  nights: TrendNight[]
  first: string
  days: number
  focus: string | null
  onFocus: (wakeOn: string) => void
}) {
  const slot = (wakeOn: string) => daysBetween(first, wakeOn)
  const x = (wakeOn: string) => (days <= 1 ? W / 2 : (slot(wakeOn) / (days - 1)) * W)

  const all = measures.flatMap((m) => nights.map(m.get)).filter((v): v is number => v !== null)
  if (all.length === 0) return <p className="trends__none">Nothing measured in this stretch.</p>
  const [low, high] = axis(all, measures[0]!)
  const y = (v: number) => H - ((v - low) / (high - low)) * H

  /** Broken wherever a night is missing, so a gap is never drawn across. */
  const path = (values: (number | null)[]) => {
    let d = ''
    let prev: string | null = null
    nights.forEach((n, i) => {
      const v = values[i]
      if (v === null || v === undefined) {
        prev = null
        return
      }
      const joined = prev !== null && slot(n.wake_on) - slot(prev) === 1
      d += `${joined ? 'L' : 'M'}${x(n.wake_on).toFixed(1)} ${y(v).toFixed(1)} `
      prev = n.wake_on
    })
    return d.trim()
  }

  const pick = (e: PointerEvent<HTMLDivElement>) => {
    const box = e.currentTarget.getBoundingClientRect()
    const at = Math.round(((e.clientX - box.left) / box.width) * (days - 1))
    const nearest = nights.reduce<TrendNight | null>(
      (best, n) =>
        best === null || Math.abs(slot(n.wake_on) - at) < Math.abs(slot(best.wake_on) - at)
          ? n
          : best,
      null,
    )
    if (nearest) onFocus(nearest.wake_on)
  }

  const focused = focus ? nights.find((n) => n.wake_on === focus) : undefined
  const primary = measures[0]!
  const avg = averaged(nights, primary.get)
  const mean = avg.filter((v): v is number => v !== null)

  return (
    <div className="trends__chart">
      {/*
        The plot in a box of its own, the width of the lines, so the picked
        night's dots, the crosshair and where a finger lands all agree. The
        scale sits in the gutter beside it.
      */}
      <div
        className="trends__plot"
        onPointerDown={pick}
        onPointerMove={(e) => {
          // A mouse picks as it moves; a finger only while it is down.
          if (e.pointerType === 'mouse' || e.buttons) pick(e)
        }}
      >
      <svg
        className="trends__svg"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={
          mean.length
            ? `${measures.map((m) => m.title).join(' and ')}, seven-night average from ${primary.show(mean[0]!)} to ${primary.show(mean[mean.length - 1]!)}`
            : `${measures.map((m) => m.title).join(' and ')}, too few nights for an average`
        }
      >
        {/* The nights themselves, then each measure's average over them. */}
        {measures.length === 1 && (
          <path
            d={path(nights.map(primary.get))}
            fill="none"
            stroke={NIGHTLY}
            strokeWidth={1}
            vectorEffect="non-scaling-stroke"
            strokeLinejoin="round"
          />
        )}
        {[...measures].reverse().map((m) => (
          <path
            key={m.key}
            d={path(measures.length === 1 ? avg : nights.map(m.get))}
            fill="none"
            stroke={m.colour}
            strokeWidth={2}
            strokeDasharray={m.key === 'room' ? '4 5' : undefined}
            vectorEffect="non-scaling-stroke"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}
        {focused && (
          <line
            x1={x(focused.wake_on)}
            x2={x(focused.wake_on)}
            y1={0}
            y2={H}
            stroke="var(--text-faint)"
            strokeWidth={1}
            vectorEffect="non-scaling-stroke"
          />
        )}
      </svg>
      {focused &&
        measures.map((m) => {
          const v = m.get(focused)
          if (v === null) return null
          return (
            <span
              key={m.key}
              className="trends__dot"
              style={{
                left: `${(x(focused.wake_on) / W) * 100}%`,
                top: `${(y(v) / H) * 100}%`,
                background: m.colour,
              }}
            />
          )
        })}
      </div>
      <span className="trends__scale trends__scale--high">{(primary.tick ?? primary.show)(high)}</span>
      <span className="trends__scale trends__scale--low">{(primary.tick ?? primary.show)(low)}</span>
    </div>
  )
}

function Tile({
  label,
  now,
  before,
  show,
  lowerIsBetter = false,
  note,
}: {
  label: string
  now: number | null
  before: number | null
  show: (v: number) => string
  lowerIsBetter?: boolean
  note?: string
}) {
  const change = now !== null && before !== null ? now - before : null
  const better = change === null || change === 0 ? null : change < 0 === lowerIsBetter
  return (
    <div className="trends__tile">
      <p className="trends__tile-label">{label}</p>
      <p className="trends__tile-value">{now === null ? '—' : show(now)}</p>
      <p
        className={`trends__tile-change${better === null ? '' : better ? ' trends__tile-change--better' : ' trends__tile-change--worse'}`}
      >
        {change === null
          ? note ?? 'Nothing to compare yet'
          : change === 0
            ? 'Same as the stretch before'
            : `${change > 0 ? '↑' : '↓'} ${show(Math.abs(change))} on the stretch before`}
      </p>
    </div>
  )
}

function Readout({ night }: { night: TrendNight }) {
  const bits: [string, string][] = []
  if (night.score !== null) bits.push([`${night.score}`, 'score'])
  if (night.deep_rem_s !== null) bits.push([minutes(night.deep_rem_s), 'deep and REM'])
  if (night.latency_s !== null) bits.push([minutes(night.latency_s), 'to fall asleep'])
  if (night.bed_c !== null) bits.push([`${night.bed_c.toFixed(1)}°`, 'bed'])
  if (night.room_c !== null) bits.push([`${night.room_c.toFixed(1)}°`, 'room'])
  if (night.kwh !== null) bits.push([`${night.kwh.toFixed(2)} kWh`, night.cost_p !== null ? money(night.cost_p) : ''])
  return (
    <div className="trends__readout" aria-live="polite">
      <p className="trends__readout-date">
        {formatDay(parseDay(night.wake_on))}
        {night.test && <span className="trends__flag">test night</span>}
        {night.tags.map((t) => (
          <span key={t} className="trends__flag">
            {t}
          </span>
        ))}
      </p>
      <p className="trends__readout-values">
        {bits.map(([value, label], i) => (
          <span key={i}>
            <b>{value}</b> {label}
          </span>
        ))}
        {bits.length === 0 && <span>Nothing measured</span>}
      </p>
    </div>
  )
}

function Tariff({
  pence,
  onSave,
}: {
  pence: number | null
  onSave: (pence: number | null) => Promise<void>
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(pence === null ? '' : String(pence))
  const [error, setError] = useState<string | null>(null)

  if (!editing) {
    return (
      <button type="button" className="trends__tariff-link" onClick={() => setEditing(true)}>
        {pence === null ? 'Set your electricity rate to see the cost' : `At ${pence}p per kWh. Change`}
      </button>
    )
  }
  return (
    <form
      className="trends__tariff"
      onSubmit={(e) => {
        e.preventDefault()
        const value = draft.trim() === '' ? null : Number(draft)
        if (value !== null && !(value > 0 && value <= 200)) {
          setError('A rate in pence per kWh, between 0 and 200.')
          return
        }
        void onSave(value)
          .then(() => setEditing(false))
          .catch((err: Error) => setError(err.message))
      }}
    >
      <label className="trends__tariff-label" htmlFor="tariff">
        Pence per kWh
      </label>
      <input
        id="tariff"
        className="trends__tariff-input"
        inputMode="decimal"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder="24.5"
      />
      <button type="submit" className="pill">
        Save
      </button>
      {error && <p className="footnote footnote--error">{error}</p>}
    </form>
  )
}

export function Trends({ client }: { client: ApiClient }) {
  const [days, setDays] = useState<TrendRange>(30)
  const [data, setData] = useState<TrendsData | null>(null)
  const [loading, setLoading] = useState(false)
  const [focus, setFocus] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    setLoading(true)
    void client
      .getTrends(days)
      .then((d) => {
        if (!live) return
        setData(d)
        setFocus(null)
      })
      .catch(() => undefined)
      .finally(() => live && setLoading(false))
    return () => {
      live = false
    }
  }, [client, days])

  const nights = data?.nights ?? []
  const shown = useMemo(
    () => (focus ? nights.find((n) => n.wake_on === focus) : nights[nights.length - 1]) ?? null,
    [focus, nights],
  )

  if (!data) return null

  const now: TrendSummary = data.summary.now
  const before: TrendSummary = data.summary.before

  const step = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight' || nights.length === 0) return
    e.preventDefault()
    const at = shown ? nights.indexOf(shown) : nights.length - 1
    const next = Math.max(0, Math.min(nights.length - 1, at + (e.key === 'ArrowLeft' ? -1 : 1)))
    setFocus(nights[next]!.wake_on)
  }

  const saveTariff = (pence: number | null) =>
    client.setTariff(pence).then(() => client.getTrends(days).then(setData))

  return (
    <>
    <Card label="Trends">
      <div className="trends__ranges" role="group" aria-label="How far back">
        {RANGES.map((r) => (
          <button
            key={r.days}
            type="button"
            className="trends__range"
            aria-pressed={days === r.days}
            onClick={() => setDays(r.days)}
          >
            {r.label}
          </button>
        ))}
      </div>

      <div className={`trends${loading ? ' trends--loading' : ''}`}>
        {nights.length === 0 ? (
          <p className="trends__none">Nothing yet. Each morning adds a night.</p>
        ) : (
          <>
            <div className="trends__tiles">
              <Tile label="Sleep score" now={now.score} before={before.score} show={(v) => `${Math.round(v)}`} />
              <Tile
                label="Deep and REM"
                now={now.deep_rem_s}
                before={before.deep_rem_s}
                show={(v) => minutes(v)}
              />
              <Tile
                label="To fall asleep"
                now={now.latency_s}
                before={before.latency_s}
                show={(v) => minutes(v)}
                lowerIsBetter
              />
              <Tile
                label={`${now.cost_p_total !== null ? 'Cost' : 'Energy'}, ${data.days === 365 ? 'the year' : `${data.days} days`}`}
                now={now.cost_p_total ?? now.kwh_total}
                before={now.cost_p_total !== null ? before.cost_p_total : before.kwh_total}
                show={now.cost_p_total !== null ? money : (v) => `${v.toFixed(1)} kWh`}
                lowerIsBetter
              />
            </div>
            <Tariff key={data.tariff_p ?? 'none'} pence={data.tariff_p} onSave={saveTariff} />

            {shown && <Readout night={shown} />}

            {/* One focus for all of them: touching any chart picks the night in every one. */}
            <div
              className="trends__charts"
              role="group"
              tabIndex={0}
              onKeyDown={step}
              aria-label="Charts. Left and right arrows move between nights."
            >
              {[
                { title: SCORE.title, measures: [SCORE] },
                { title: DEEP_REM.title, measures: [DEEP_REM] },
                { title: LATENCY.title, measures: [LATENCY] },
                { title: 'Bed and room', measures: [BED_C, ROOM_C], key: true },
                { title: ENERGY.title, measures: [ENERGY] },
              ].map((c) => (
                <section key={c.title} className="trends__small">
                  <h3 className="trends__title">
                    {c.title}
                    {c.key ? (
                      <span className="trends__key">
                        {c.measures.map((m) => (
                          <span key={m.key} className="trends__key-item">
                            <span
                              className={`trends__swatch${m.key === 'room' ? ' trends__swatch--dashed' : ''}`}
                              style={{ background: m.key === 'room' ? undefined : m.colour, borderColor: m.colour }}
                            />
                            {m.title}
                          </span>
                        ))}
                      </span>
                    ) : (
                      <span className="trends__key">7-night average</span>
                    )}
                  </h3>
                  <Chart
                    measures={c.measures}
                    nights={nights}
                    first={data.first}
                    days={data.days}
                    focus={shown?.wake_on ?? null}
                    onFocus={setFocus}
                  />
                </section>
              ))}
              <div className="chart__axis trends__axis">
                <span>{formatDay(parseDay(data.first))}</span>
                <span>{formatDay(parseDay(data.last))}</span>
              </div>
            </div>
          </>
        )}
      </div>

      <p className="footnote">
        Averages a night, and the {data.days === 365 ? "year's" : `${data.days} days'`}{' '}
        {data.tariff_p !== null ? 'cost' : 'energy'} in all, set against the same stretch before.
        Tagged nights stay in here; only the scoreboard leaves some out.
      </p>
    </Card>

      {/* Every value on the charts, readable without touching them. */}
      {nights.length > 0 && (
        <Fold id="trends-table" label="Every night" summary={`${nights.length} nights`}>
          <div className="trends__table-wrap">
            <table className="trends__table">
              <thead>
                <tr>
                  <th scope="col">Night</th>
                  <th scope="col">Score</th>
                  <th scope="col">Deep+REM</th>
                  <th scope="col">Asleep in</th>
                  <th scope="col">Bed</th>
                  <th scope="col">Room</th>
                  <th scope="col">kWh</th>
                  {data.tariff_p !== null && <th scope="col">Cost</th>}
                </tr>
              </thead>
              <tbody>
                {[...nights].reverse().map((n) => (
                  <tr key={n.wake_on}>
                    <th scope="row">
                      {formatDay(parseDay(n.wake_on))}
                      {n.left_out && ' *'}
                    </th>
                    <td>{n.score ?? '—'}</td>
                    <td>{n.deep_rem_s === null ? '—' : minutes(n.deep_rem_s)}</td>
                    <td>{n.latency_s === null ? '—' : minutes(n.latency_s)}</td>
                    <td>{n.bed_c === null ? '—' : n.bed_c.toFixed(1)}</td>
                    <td>{n.room_c === null ? '—' : n.room_c.toFixed(1)}</td>
                    <td>{n.kwh === null ? '—' : n.kwh.toFixed(2)}</td>
                    {data.tariff_p !== null && <td>{n.cost_p === null ? '—' : money(n.cost_p)}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {nights.some((n) => n.left_out) && (
            <p className="footnote">* Tagged with something that leaves it out of the scoreboard.</p>
          )}
        </Fold>
      )}
    </>
  )
}
