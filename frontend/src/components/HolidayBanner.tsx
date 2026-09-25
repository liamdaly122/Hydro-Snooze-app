import { Suitcase } from './Icons'
import { daysBetween, formatDay, homeNow, isoDay, parseDay } from '../domain'
import type { Holiday } from '../types'

/** How far ahead a holiday starts showing on the home screen. */
const SHOWS_FROM_DAYS = 7

/**
 * One line on the home screen while a holiday is close or under way.
 *
 * Nothing to tap. Holiday mode is set and changed from the menu, and a control
 * for it here is exactly what keeping it off the home screen is for. But the
 * screen still has to say it, or the Alarm card's quiet "Bed Mon 12 Oct" reads as
 * a fault rather than a decision, and a bed that is not getting ready tonight
 * looks broken to somebody who has forgotten they are not supposed to be here.
 *
 * Only from a week out. One set in March for August does not need a banner in
 * March: a banner that is always there is a banner nobody reads.
 */
export function HolidayBanner({ holiday, now = homeNow() }: { holiday: Holiday | null; now?: Date }) {
  if (!holiday) return null
  const today = isoDay(now)
  if (daysBetween(today, holiday.leaves_on) > SHOWS_FROM_DAYS) return null

  const leaves = formatDay(parseDay(holiday.leaves_on))
  const back = formatDay(parseDay(holiday.back_on))
  let sub: string
  if (today < holiday.leaves_on) sub = `Nothing switches on from ${leaves}. Back ${back}`
  else if (today === holiday.leaves_on) sub = `Nothing switches on from tonight. Back ${back}`
  else if (today < holiday.back_on) sub = `Nothing switches on until you are back, ${back}`
  else sub = 'Back today, so it ends tonight'

  return (
    <div className="holiday-banner" role="status">
      <span className="holiday-banner__icon">
        <Suitcase size={16} />
      </span>
      <span className="tonight__text">
        <span className="tonight__title">Holiday mode</span>
        <span className="tonight__sub">{sub}</span>
      </span>
    </div>
  )
}
