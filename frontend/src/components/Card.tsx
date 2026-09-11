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
  action,
}: {
  label: string
  children: ReactNode
  onOpen?: () => void
  openLabel?: string
  /**
   * Something in the header, to the right of the label. For a card whose action
   * belongs to the card rather than to a row inside it.
   *
   * Here rather than positioned absolutely by the caller, because `.card` is not
   * a positioning context and making it one would move `.stage::before` and
   * `.stage__note`, which are anchored to a stage inside the Temperature card.
   */
  action?: ReactNode
}) {
  return (
    <section className="card">
      <header className="card__head">
        <h2 className="card__label">{label}</h2>
        {action}
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
