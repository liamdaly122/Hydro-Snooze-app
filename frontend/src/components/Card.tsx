import type { ReactNode } from 'react'
import { ChevronRight } from './Icons'

/**
 * The one card shape the whole app uses: an uppercase letter-spaced label, a
 * hairline rule, then content.
 *
 * The chevron only appears when there is somewhere to go. It used to be on every
 * card and do nothing, which is worse than not having one: a control that looks
 * tappable and is not teaches you to stop tapping.
 */
export function Card({
  label,
  children,
  onOpen,
  openLabel,
}: {
  label: string
  children: ReactNode
  onOpen?: () => void
  openLabel?: string
}) {
  return (
    <section className="card">
      <header className="card__head">
        <h2 className="card__label">{label}</h2>
        {onOpen && (
          <button
            type="button"
            className="card__open"
            aria-label={openLabel ?? `Open ${label}`}
            onClick={onOpen}
          >
            <ChevronRight className="card__chevron" />
          </button>
        )}
      </header>
      {children}
    </section>
  )
}
