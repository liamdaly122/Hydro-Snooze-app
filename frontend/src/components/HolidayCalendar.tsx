import { useState } from 'react'
import { ChevronRight } from './Icons'
import { DAY_INITIALS, MONTH_LONG, formatDay, isoDay, mondayFirstDay, parseDay } from '../domain'

interface Props {
  /** "YYYY-MM-DD", or null before anything is picked. */
  leaves: string | null
  back: string | null
  /** The earliest day that can be picked. */
  from: string
  onPick: (leaves: string | null, back: string | null) => void
}

/**
 * A month at a time, picked the way a hotel booking is: the day you leave, then
 * the day you get back, with the nights in between shaded.
 *
 * Not two native date inputs. Those are fine for one day and poor for a range,
 * because neither can see the other, so nothing shows how long the gap is until
 * both have been set and read back. Here the stretch you will be away is the
 * thing on screen.
 *
 * A third tap starts again from that day. Changing only the day back means
 * tapping both, which is one tap more than strictly needed and far easier to
 * predict than a picker that guesses which end you meant.
 */
export function HolidayCalendar({ leaves, back, from, onPick }: Props) {
  const opening = parseDay(leaves ?? from)
  const [year, setYear] = useState(opening.getFullYear())
  const [month, setMonth] = useState(opening.getMonth())

  const earliest = parseDay(from)
  const atEarliest = year === earliest.getFullYear() && month === earliest.getMonth()
  const today = isoDay(new Date())

  function step(by: number) {
    const next = new Date(year, month + by, 1)
    setYear(next.getFullYear())
    setMonth(next.getMonth())
  }

  function pick(day: string) {
    if (leaves === null || back !== null || day <= leaves) {
      onPick(day, null)
      return
    }
    onPick(leaves, day)
  }

  const first = new Date(year, month, 1)
  const blanks = mondayFirstDay(first)
  const length = new Date(year, month + 1, 0).getDate()
  const days = Array.from({ length }, (_, i) => isoDay(new Date(year, month, i + 1)))

  return (
    <div className="cal">
      <div className="cal__nav">
        <button
          type="button"
          className="cal__step"
          onClick={() => step(-1)}
          disabled={atEarliest}
          aria-label="Previous month"
        >
          <ChevronRight className="cal__back" />
        </button>
        <span className="cal__month" aria-live="polite">
          {MONTH_LONG[month]} {year}
        </span>
        <button type="button" className="cal__step" onClick={() => step(1)} aria-label="Next month">
          <ChevronRight />
        </button>
      </div>

      <div className="cal__grid" role="group" aria-label={`${MONTH_LONG[month]} ${year}`}>
        {DAY_INITIALS.map((initial, i) => (
          <span key={`dow-${i}`} className="cal__dow" aria-hidden="true">
            {initial}
          </span>
        ))}
        {Array.from({ length: blanks }, (_, i) => (
          <span key={`blank-${i}`} />
        ))}
        {days.map((day) => {
          const isStart = day === leaves
          const isEnd = day === back
          const between = leaves !== null && back !== null && day > leaves && day < back
          const classes = [
            'cal__day',
            isStart && 'is-start',
            isStart && back !== null && 'has-end',
            isEnd && 'is-end',
            between && 'is-between',
            day === today && 'is-today',
          ]
            .filter(Boolean)
            .join(' ')
          const date = parseDay(day)
          const role = isStart ? ', leaving' : isEnd ? ', back' : between ? ', away' : ''
          return (
            <button
              key={day}
              type="button"
              className={classes}
              disabled={day < from}
              aria-pressed={isStart || isEnd}
              aria-label={`${formatDay(date)}${role}`}
              onClick={() => pick(day)}
            >
              <span>{date.getDate()}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
