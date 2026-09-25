import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { InfoIcon } from './Icons'

/**
 * A small "i" beside a card's title, and what it opens: the card's explanation
 * in a sheet from the bottom of the screen.
 *
 * This is where the footnotes went. Each was worth reading once and in the way
 * every time after, and on the Autopilot screen they had grown to half the page.
 * Nothing in one of these sheets is needed to use the card; everything in them
 * is there for the day somebody wonders what a number means.
 */
export function InfoButton({ title, children }: { title: string; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button
        type="button"
        className="info-btn"
        aria-label={`About ${title.toLowerCase()}`}
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
      >
        <InfoIcon />
      </button>
      {open && (
        <InfoSheet title={title} onClose={() => setOpen(false)}>
          {children}
        </InfoSheet>
      )}
    </>
  )
}

function InfoSheet({
  title,
  onClose,
  children,
}: {
  title: string
  onClose: () => void
  children: ReactNode
}) {
  const id = useId()
  const done = useRef<HTMLButtonElement>(null)
  // Held in a ref so a parent re-rendering, which hands over a new function
  // every time, does not re-run the effect and yank the focus about.
  const close = useRef(onClose)
  close.current = onClose

  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    done.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close.current()
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      opener?.focus()
    }
  }, [])

  // To the body, not inside the card: a card with a blur on it would otherwise
  // become what "fixed" is measured against, and the sheet would open inside it.
  return createPortal(
    <div className="scrim info-scrim" onClick={() => close.current()}>
      <div
        className="sheet info-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby={id}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id={id} className="sheet__title">
          {title}
        </h2>
        <div className="sheet__body info-sheet__body">{children}</div>
        <div className="sheet__actions">
          <button ref={done} type="button" className="sheet__btn" onClick={() => close.current()}>
            Done
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
