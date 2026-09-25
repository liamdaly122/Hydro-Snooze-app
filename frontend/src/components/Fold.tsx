import { useState, type ReactNode } from 'react'
import { ChevronRight } from './Icons'

/**
 * A card that folds down to its title and one line saying where it is up to.
 *
 * For the cards worth a glance every morning and a proper read now and then,
 * which on the Autopilot screen is Sleep timing and Learning. Folded, each is a
 * row: "2 suggestions ready", "Everything measured". Open, it is the whole card.
 *
 * Remembered per phone, so a card left open stays open. Only a convenience: a
 * private window, or storage the browser refuses, just starts folded.
 */

const KEY = 'hs.fold.'

function remembered(id: string): boolean | null {
  try {
    const held = localStorage.getItem(KEY + id)
    return held === null ? null : held === '1'
  } catch {
    return null
  }
}

function remember(id: string, open: boolean): void {
  try {
    localStorage.setItem(KEY + id, open ? '1' : '0')
  } catch {
    // Nowhere to keep it. It folds again next time, which is fine.
  }
}

export function Fold({
  id,
  label,
  summary,
  info,
  peek,
  defaultOpen = false,
  children,
}: {
  id: string
  label: string
  /** One line beside the title, folded or not. */
  summary?: ReactNode
  /** An InfoButton, outside the fold's own button so tapping it opens the sheet only. */
  info?: ReactNode
  /** Shown under the title whether the card is open or not. */
  peek?: ReactNode
  defaultOpen?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(() => remembered(id) ?? defaultOpen)
  const body = `fold-${id}`

  return (
    <section className={`card fold${open ? ' fold--open' : ''}`}>
      <div className="fold__head">
        <h2 className="fold__heading">
          <button
            type="button"
            className="fold__toggle"
            aria-expanded={open}
            aria-controls={body}
            onClick={() => {
              setOpen(!open)
              remember(id, !open)
            }}
          >
            <span className="fold__titles">
              <span className="card__label">{label}</span>
              {summary && <span className="fold__summary">{summary}</span>}
            </span>
            <ChevronRight className="fold__chevron" />
          </button>
        </h2>
        {info}
      </div>
      {peek}
      <div id={body} className="fold__body" hidden={!open}>
        {children}
      </div>
    </section>
  )
}
