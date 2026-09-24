import type { Verdict } from '../types'

/**
 * The small coloured word under a number: Good, Fair, Low, In range, Learning.
 *
 * Three colours and a grey, and never more. Green is fine, amber is worth a
 * look, red is the one to act on, and grey is "not enough nights yet", which is
 * not a judgement of anything.
 */

const TONE: Record<Verdict, 'good' | 'fair' | 'low' | 'quiet'> = {
  excellent: 'good',
  good: 'good',
  in_range: 'good',
  fair: 'fair',
  above: 'fair',
  below: 'fair',
  low: 'low',
  learning: 'quiet',
}

export function VerdictPill({
  verdict,
  label,
  tone,
}: {
  verdict?: Verdict | null
  label: string
  /** Straight to a colour, for the targets, which are met or not. */
  tone?: 'good' | 'fair' | 'low' | 'quiet'
}) {
  const shade = tone ?? (verdict ? TONE[verdict] : 'quiet')
  return <span className={`verdict verdict--${shade}`}>{label}</span>
}
