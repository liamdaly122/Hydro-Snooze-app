import type { Verdict } from '../types'
import { VerdictPill } from './VerdictPill'

/**
 * The big number on the Health Report: Withings' sleep score on a 270 degree arc,
 * open at the bottom, the way the Eight Sleep report draws its own.
 *
 * One circle drawn twice with a dash rather than an arc path, so the rounded
 * ends come for free and the fill is a single number. The glow underneath is the
 * same stroke again, blurred, and it is what makes the arc look lit rather than
 * painted.
 */

const SIZE = 240
const STROKE = 15
const R = (SIZE - STROKE) / 2 - 8
const C = SIZE / 2
const CIRCUMFERENCE = 2 * Math.PI * R
const SWEEP = 270 / 360
const ARC = CIRCUMFERENCE * SWEEP
/** Where the arc starts: bottom left, measured clockwise from three o'clock. */
const START_DEG = 135

export function ScoreGauge({
  value,
  verdict,
  label,
}: {
  value: number | null
  verdict: Verdict | null
  label: string | null
}) {
  const filled = value === null ? 0 : (ARC * Math.max(0, Math.min(100, value))) / 100
  const turn = `rotate(${START_DEG} ${C} ${C})`

  return (
    <div className="gauge">
      <svg
        className="gauge__svg"
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        role="img"
        aria-label={value === null ? 'No score for this night' : `Sleep score ${value} out of 100`}
      >
        <defs>
          <linearGradient id="gauge-fill" x1="0" y1="1" x2="1" y2="0">
            <stop offset="0%" stopColor="#1d4ed8" />
            <stop offset="55%" stopColor="#2f6bff" />
            <stop offset="100%" stopColor="#5b93ff" />
          </linearGradient>
          <filter id="gauge-glow" x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur stdDeviation="7" />
          </filter>
        </defs>

        <circle
          cx={C}
          cy={C}
          r={R}
          fill="none"
          stroke="#1c1c22"
          strokeWidth={STROKE}
          strokeLinecap="round"
          strokeDasharray={`${ARC} ${CIRCUMFERENCE}`}
          transform={turn}
        />
        {filled > 0 && (
          <>
            <circle
              cx={C}
              cy={C}
              r={R}
              fill="none"
              stroke="#2f6bff"
              strokeWidth={STROKE + 6}
              strokeLinecap="round"
              strokeDasharray={`${filled} ${CIRCUMFERENCE}`}
              transform={turn}
              filter="url(#gauge-glow)"
              opacity={0.55}
            />
            <circle
              cx={C}
              cy={C}
              r={R}
              fill="none"
              stroke="url(#gauge-fill)"
              strokeWidth={STROKE}
              strokeLinecap="round"
              strokeDasharray={`${filled} ${CIRCUMFERENCE}`}
              transform={turn}
            />
          </>
        )}
      </svg>

      <div className="gauge__middle">
        <p className="gauge__value">{value ?? '—'}</p>
        {label && <VerdictPill verdict={verdict} label={label} />}
      </div>
    </div>
  )
}
