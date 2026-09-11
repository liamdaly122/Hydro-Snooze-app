/** Inline SVG only. No icon dependency, and nothing to fetch on a cold start. */

interface IconProps {
  size?: number
  className?: string
}

const stroke = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
}

export function ChevronRight({ size = 16, className }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className={className} aria-hidden="true">
      <path d="M9 5l7 7-7 7" {...stroke} strokeWidth={2} />
    </svg>
  )
}

export function Minus({ size = 22 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 12h14" {...stroke} strokeWidth={1.8} />
    </svg>
  )
}

export function Plus({ size = 22 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 5v14M5 12h14" {...stroke} strokeWidth={1.8} />
    </svg>
  )
}

/** IEC 5009, the symbol on every appliance, so it needs no label to be read. */
export function Power({ size = 19 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 3.5v8.5" {...stroke} strokeWidth={2} />
      <path d="M18.4 6.8a9 9 0 1 1-12.8 0" {...stroke} strokeWidth={2} />
    </svg>
  )
}

export function Clock({ size = 15 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="13" r="8" {...stroke} />
      <path d="M12 9.5V13l2.5 1.5M5 4.5L7.5 2.5M19 4.5L16.5 2.5" {...stroke} />
    </svg>
  )
}

export function Snowflake({ size = 15 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M12 3v18M4.2 7.5l15.6 9M19.8 7.5l-15.6 9M12 7l-2.4-2M12 7l2.4-2M12 17l-2.4 2M12 17l2.4 2"
        {...stroke}
      />
    </svg>
  )
}

export function Waves({ size = 15 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M3 9c2.5-2 4-2 6.5 0s4 2 6.5 0 4-2 5 -.6M3 15c2.5-2 4-2 6.5 0s4 2 6.5 0 4-2 5-.6" {...stroke} />
    </svg>
  )
}

export function Flame({ size = 15 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M12 2.5c3.5 3.5 5.5 6.2 5.5 9.4a5.5 5.5 0 01-11 0c0-1.6.6-3 1.8-4.4.3 1.4 1 2.2 2 2.4-.3-2.6.3-5 1.7-7.4z"
        {...stroke}
      />
    </svg>
  )
}

export function Bolt({ size = 15 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M13 2L4.5 13.5H11l-1 8.5L18.5 10.5H12z" {...stroke} />
    </svg>
  )
}

export function HomeIcon({ size = 25 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M3.6 10.3L12 3.5l8.4 6.8V20a1 1 0 01-1 1H4.6a1 1 0 01-1-1z" fill="currentColor" />
    </svg>
  )
}

export function ChartIcon({ size = 25 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <rect x="3.5" y="12" width="4" height="8.5" rx="1.2" fill="currentColor" />
      <rect x="10" y="6" width="4" height="14.5" rx="1.2" fill="currentColor" />
      <rect x="16.5" y="9.5" width="4" height="11" rx="1.2" fill="currentColor" />
    </svg>
  )
}

export function WrenchIcon({ size = 24 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M15.5 3a5.5 5.5 0 00-5.1 7.6L3 18l3 3 7.4-7.4A5.5 5.5 0 1015.5 3z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
      <circle cx="15.5" cy="8.5" r="1.6" fill="currentColor" />
    </svg>
  )
}

/**
 * The Autopilot mark: a four pointed star with concave sides.
 *
 * Drawn rather than fetched, and drawn with cubic curves rather than straight
 * edges, because the straight-edged version reads as a compass rose and the
 * curved one reads as a glint. `currentColor` by default so it takes the tint of
 * whatever it sits on, and a gradient when `glow` is set, which is what the hero
 * uses.
 */
export function Sparkle({ size = 24, className, glow = false }: IconProps & { glow?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className={className} aria-hidden="true">
      {glow && (
        <defs>
          <linearGradient id="sparkle-glow" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#dbe6ff" />
            <stop offset="55%" stopColor="#8fb2ff" />
            <stop offset="100%" stopColor="#5d84f5" />
          </linearGradient>
        </defs>
      )}
      <path
        d="M12 1.2c.55 5.3 5.5 10.25 10.8 10.8-5.3.55-10.25 5.5-10.8 10.8-.55-5.3-5.5-10.25-10.8-10.8C6.5 11.45 11.45 6.5 12 1.2Z"
        fill={glow ? 'url(#sparkle-glow)' : 'currentColor'}
      />
    </svg>
  )
}

/** Beside a boost. The one place in this app that talks about sleep. */
export function Moon({ size = 15 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M20.5 14.6A8.8 8.8 0 0 1 9.4 3.5a8.8 8.8 0 1 0 11.1 11.1Z"
        fill="currentColor"
      />
    </svg>
  )
}

/** Closes a pushed screen. */
export function Close({ size = 20, className }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className={className} aria-hidden="true">
      <path
        d="M5 5l14 14M19 5L5 19"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  )
}
