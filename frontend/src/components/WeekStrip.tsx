import { useEffect, useLayoutEffect, useMemo, useRef } from 'react'
import type { HealthDay } from '../types'

/**
 * The weeks along the top of the Health Report: one ring a night, filled by its
 * score, and the night being read marked with a dot underneath.
 *
 * A morning the mat has nothing for is an empty ring and cannot be picked,
 * rather than a zero, because a night nobody measured is not a bad night.
 *
 * Every week is in the strip at once and it scrolls sideways under the finger,
 * snapping to a week when it comes to rest. It used to be one week drawn at a
 * time, with a swipe measured from where the finger went down to where it came
 * up, and only then a round trip to the Pi for the next week: nothing moved
 * while the finger did, and then everything changed at once. Now the scrolling
 * is the browser's own, and nothing is asked of the Pi until it stops.
 *
 * Coming to rest on another week reads that week's latest night, as the swipe
 * did. A week with no nights is only looked at: the report stays where it was.
 */

const RING = 40
const STROKE = 3.2
const R = (RING - STROKE) / 2
const CIRCUMFERENCE = 2 * Math.PI * R

/** How long the strip has to be still before it counts as having come to rest.
 *  Long enough to outlast the gaps between scroll events while it glides. */
const SETTLE_MS = 140

/** And it has to be sitting on a week, not between two. A finger held still part
 *  way through a swipe is still, and is not at rest. */
const ON_A_WEEK_PX = 2

function letter(date: string): string {
  // Noon, so no timezone can push it onto the neighbouring day.
  return 'SMTWTFS'[new Date(`${date}T12:00:00`).getDay()]!
}

export function WeekStrip({
  days,
  selected,
  around,
  onPick,
  onWeek,
}: {
  /** Whole weeks, Sunday first. */
  days: HealthDay[]
  selected: string | null
  /** The morning the report is about. Its week is the one kept in view. */
  around: string
  onPick: (date: string) => void
  /** Came to rest on a week other than the one the report is about. */
  onWeek?: (week: HealthDay[]) => void
}) {
  const strip = useRef<HTMLDivElement>(null)
  const weeks = useMemo(() => {
    const out: HealthDay[][] = []
    for (let i = 0; i < days.length; i += 7) out.push(days.slice(i, i + 7))
    return out
  }, [days])
  const shown = Math.max(0, weeks.findIndex((w) => w.some((d) => d.date === around)))
  // The same days again after a reload are not new days: only a change in what
  // the strip holds puts it back on the report's week without a glide.
  const holding = `${days[0]?.date}:${days.length}`
  const placed = useRef<string | null>(null)
  const resting = useRef<number | undefined>(undefined)

  // The report's week in view: straight there when the strip is first drawn or
  // its weeks change, so it never slides in from the first week; gliding when
  // the report moves to a week that is not the one on screen.
  useLayoutEffect(() => {
    const el = strip.current
    if (!el) return
    const left = shown * el.clientWidth
    if (placed.current !== holding) {
      placed.current = holding
      el.scrollLeft = left
    } else if (Math.round(el.scrollLeft / el.clientWidth) !== shown) {
      el.scrollTo({ left, behavior: 'smooth' })
    }
  }, [shown, holding])

  useEffect(() => () => window.clearTimeout(resting.current), [])

  const scrolled = () => {
    window.clearTimeout(resting.current)
    resting.current = window.setTimeout(() => {
      const el = strip.current
      if (!el || el.clientWidth === 0) return
      const at = Math.round(el.scrollLeft / el.clientWidth)
      if (Math.abs(el.scrollLeft - at * el.clientWidth) > ON_A_WEEK_PX) return
      const week = weeks[at]
      if (week && at !== shown) onWeek?.(week)
    }, SETTLE_MS)
  }

  return (
    <div className="week" ref={strip} onScroll={scrolled}>
      {weeks.map((week) => (
        <div className="week__page" key={week[0]!.date}>
          {week.map((day) => (
            <Day key={day.date} day={day} picked={day.date === selected} onPick={onPick} />
          ))}
        </div>
      ))}
    </div>
  )
}

function Day({
  day,
  picked,
  onPick,
}: {
  day: HealthDay
  picked: boolean
  onPick: (date: string) => void
}) {
  const filled = day.score === null ? 0 : (CIRCUMFERENCE * Math.min(100, day.score)) / 100
  return (
    <button
      type="button"
      className={`week__day${picked ? ' week__day--picked' : ''}`}
      disabled={!day.has_night}
      aria-pressed={picked}
      aria-label={
        day.has_night
          ? `${new Date(`${day.date}T12:00:00`).toLocaleDateString('en-GB', {
              weekday: 'long',
              day: 'numeric',
              month: 'long',
            })}, score ${day.score ?? 'unknown'}`
          : `No night for ${day.date}`
      }
      onClick={() => onPick(day.date)}
    >
      <svg width={RING} height={RING} viewBox={`0 0 ${RING} ${RING}`} aria-hidden="true">
        <circle
          cx={RING / 2}
          cy={RING / 2}
          r={R}
          fill="none"
          stroke="#26262c"
          strokeWidth={STROKE}
        />
        {filled > 0 && (
          <circle
            cx={RING / 2}
            cy={RING / 2}
            r={R}
            fill="none"
            stroke={picked ? '#4f86ff' : '#2a5bd7'}
            strokeWidth={STROKE}
            strokeLinecap="round"
            strokeDasharray={`${filled} ${CIRCUMFERENCE}`}
            transform={`rotate(-90 ${RING / 2} ${RING / 2})`}
          />
        )}
      </svg>
      <span className="week__letter">{letter(day.date)}</span>
      <span className="week__dot" />
    </button>
  )
}
