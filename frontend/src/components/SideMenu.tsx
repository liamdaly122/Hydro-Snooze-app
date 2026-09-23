import { useEffect, useRef } from 'react'
import { ChevronRight, Close, Suitcase } from './Icons'
import { formatDay, parseDay } from '../domain'
import type { Holiday } from '../types'

/**
 * The things worth having and not worth a place on the home screen.
 *
 * Holiday mode is the first. It is set a few times a year, days ahead, and it
 * switches the bed off for a week: exactly the sort of control that should be
 * two deliberate taps away rather than one careless one. The home screen is for
 * tonight.
 */
export function SideMenu({
  open,
  onClose,
  holiday,
  onOpenHoliday,
}: {
  open: boolean
  onClose: () => void
  holiday: Holiday | null
  onOpenHoliday: () => void
}) {
  const first = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    // Focus into the menu, so a keyboard or VoiceOver lands on what just opened
    // rather than on the button behind it.
    first.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="drawer-scrim" onClick={onClose}>
      <nav
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-label="Menu"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="drawer__head">
          <span className="drawer__title">HydroSnooze</span>
          <button type="button" className="drawer__close" onClick={onClose} aria-label="Close the menu">
            <Close />
          </button>
        </div>

        <button type="button" className="drawer__item" ref={first} onClick={onOpenHoliday}>
          <span className="drawer__icon">
            <Suitcase />
          </span>
          <span className="drawer__text">
            <span className="drawer__label">Holiday mode</span>
            <span className={`drawer__sub${holiday ? ' drawer__sub--on' : ''}`}>
              {holiday
                ? `On, ${formatDay(parseDay(holiday.leaves_on))} to ${formatDay(parseDay(holiday.back_on))}`
                : 'Off'}
            </span>
          </span>
          <ChevronRight className="drawer__chevron" />
        </button>
      </nav>
    </div>
  )
}
