import { useRef } from 'react'
import type { HealthDay } from '../types'

/**
 * The week along the top of the Health Report: one ring a night, filled by its
 * score, and the night being read marked with a dot underneath.
 *
 * A morning the mat has nothing for is an empty ring and cannot be picked,
 * rather than a zero, because a night nobody measured is not a bad night.
 *
 * Swiping sideways steps a week at a time. There is nothing drawn for it,
 * which is how the reference does it too; the rings are the thing to look at.
 */

const RING = 40
const STROKE = 3.2
const R = (RING - STROKE) / 2
const CIRCUMFERENCE = 2 * Math.PI * R

/** A swipe has to travel this far sideways, and further sideways than down. */
const SWIPE_PX = 45

function letter(date: string): string {
  // Noon, so no timezone can push it onto the neighbouring day.
  return 'SMTWTFS'[new Date(`${date}T12:00:00`).getDay()]!
}

export function WeekStrip({
  days,
  selected,
  onPick,
  onPreviousWeek,
  onNextWeek,
}: {
  days: HealthDay[]
  selected: string | null
  onPick: (date: string) => void
  onPreviousWeek?: () => void
  onNextWeek?: () => void
}) {
  const from = useRef<{ x: number; y: number } | null>(null)

  return (
    <div
      className="week"
      onTouchStart={(e) => {
        const t = e.touches[0]
        from.current = t ? { x: t.clientX, y: t.clientY } : null
      }}
      onTouchEnd={(e) => {
        const start = from.current
        const t = e.changedTouches[0]
        from.current = null
        if (!start || !t) return
        const dx = t.clientX - start.x
        const dy = t.clientY - start.y
        if (Math.abs(dx) < SWIPE_PX || Math.abs(dx) < Math.abs(dy)) return
        if (dx > 0) onPreviousWeek?.()
        else onNextWeek?.()
      }}
    >
      {days.map((day) => {
        const picked = day.date === selected
        const filled = day.score === null ? 0 : (CIRCUMFERENCE * Math.min(100, day.score)) / 100
        return (
          <button
            key={day.date}
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
      })}
    </div>
  )
}
