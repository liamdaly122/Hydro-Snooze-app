import type { ReactNode } from 'react'
import { ChevronRight } from './Icons'

/**
 * The one card shape the whole app uses: an uppercase letter-spaced label, a
 * chevron, a hairline rule, then content.
 */
export function Card({
  label,
  children,
  chevron = true,
}: {
  label: string
  children: ReactNode
  chevron?: boolean
}) {
  return (
    <section className="card">
      <header className="card__head">
        <h2 className="card__label">{label}</h2>
        {chevron && <ChevronRight className="card__chevron" />}
      </header>
      {children}
    </section>
  )
}
