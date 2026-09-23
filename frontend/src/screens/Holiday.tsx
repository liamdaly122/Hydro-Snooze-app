import { useEffect, useState } from 'react'
import { Card } from '../components/Card'
import { HolidayCalendar } from '../components/HolidayCalendar'
import {
  daysBetween,
  firstNightBack,
  formatDay,
  formatTime,
  isoDay,
  parseDay,
  scheduledNightsAway,
} from '../domain'
import type { ApiClient } from '../api/client'
import type { Holiday as HolidayType, Schedule } from '../types'

interface Props {
  client: ApiClient
  schedule: Schedule
  holiday: HolidayType | null
  onChanged: (holiday: HolidayType | null) => void
}

function nightsOff(n: number): string {
  return n === 1 ? '1 night off' : `${n} nights off`
}

/**
 * Away from home, with nothing switching on while you are gone.
 *
 * Off the home screen on purpose, behind the menu. It is set a few times a year
 * and switches the bed off for days at a time, so it belongs somewhere you go
 * deliberately rather than somewhere a thumb can land on it at midnight.
 *
 * The routine is not touched. Days, times and temperatures are exactly where
 * they were, so there is nothing to put back afterwards, and it switches itself
 * off once you are home.
 */
export function Holiday({ client, schedule, holiday, onChanged }: Props) {
  const today = isoDay(new Date())
  const [leaves, setLeaves] = useState<string | null>(holiday?.leaves_on ?? null)
  const [back, setBack] = useState<string | null>(holiday?.back_on ?? null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Follow the saved dates when they change from somewhere else: another
  // phone, or the holiday ending. Keyed on the two strings, so a refetch that
  // brings back the same dates leaves a pair being picked alone.
  useEffect(() => {
    setLeaves(holiday?.leaves_on ?? null)
    setBack(holiday?.back_on ?? null)
  }, [holiday?.leaves_on, holiday?.back_on])

  const picked: HolidayType | null =
    leaves && back ? { leaves_on: leaves, back_on: back, nights: daysBetween(leaves, back) } : null
  const unchanged =
    holiday !== null && picked !== null &&
    holiday.leaves_on === picked.leaves_on && holiday.back_on === picked.back_on

  function save() {
    if (!picked) return
    setBusy(true)
    setError(null)
    void client
      .setHoliday(picked.leaves_on, picked.back_on)
      .then(onChanged)
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false))
  }

  function turnOff() {
    setBusy(true)
    setError(null)
    void client
      .clearHoliday()
      .then(() => onChanged(null))
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false))
  }

  return (
    <>
      <Card label="Holiday mode">
        <div className="skip">
          <span className="skip__text">
            <span className="skip__title">
              {holiday
                ? `Away ${formatDay(parseDay(holiday.leaves_on))} to ${formatDay(parseDay(holiday.back_on))}`
                : 'Off'}
            </span>
            <span className="skip__sub">
              {holiday
                ? `${nightsOff(holiday.nights)}. ${comingBack(schedule, holiday)}`
                : 'Pick the day you leave and the day you get back. Nothing switches on for the nights in between.'}
            </span>
          </span>
          {holiday && (
            <button type="button" className="pill" onClick={turnOff} disabled={busy}>
              Turn off
            </button>
          )}
        </div>
      </Card>

      <Card label={holiday ? 'Change the dates' : 'Dates'}>
        <HolidayCalendar
          leaves={leaves}
          back={back}
          from={today}
          onPick={(l, b) => {
            setError(null)
            setLeaves(l)
            setBack(b)
          }}
        />

        <div className="timerow">
          <span className="timerow__label">Leaving</span>
          <span className={`holiday__day${leaves ? '' : ' holiday__day--unset'}`}>
            {leaves ? formatDay(parseDay(leaves)) : 'Pick a day'}
          </span>
        </div>
        <div className="timerow">
          <span className="timerow__label">Back</span>
          <span className={`holiday__day${back ? '' : ' holiday__day--unset'}`}>
            {back ? formatDay(parseDay(back)) : leaves ? 'Now pick this one' : 'Pick a day'}
          </span>
        </div>

        <p className="footnote">{whatThatMeans(schedule, picked, leaves)}</p>

        <button
          type="button"
          className="rehearsal__btn holiday__save"
          disabled={!picked || unchanged || busy}
          onClick={save}
        >
          {holiday ? 'Save these dates' : 'Turn on holiday mode'}
        </button>
      </Card>

      {error && (
        <p className="footnote footnote--error" onClick={() => setError(null)}>
          {error}
        </p>
      )}

      <p className="footnote">
        The weekly schedule is left exactly as it is, and holiday mode switches itself off once you
        are back. If a night away has already started when you set it, the unit is switched off
        straight away. Home early? Turn it off here and the next night runs as usual.
      </p>
    </>
  )
}

/** When the bed runs again, which is not always the night you get back. */
function comingBack(schedule: Schedule, holiday: HolidayType): string {
  if (!schedule.enabled) {
    return 'Run automatically is off as well, so nothing runs afterwards until that is back on.'
  }
  const plan = firstNightBack(schedule, holiday)
  if (!plan) return ''
  return `The bed runs again ${formatDay(plan.bedtimeAt)}, lights out at ${formatTime(plan.bedtimeAt)}.`
}

/** The sentence under the two rows, for whatever has been picked so far. */
function whatThatMeans(schedule: Schedule, picked: HolidayType | null, leaves: string | null): string {
  if (!leaves) return 'Tap the day you leave, then the day you get back.'
  if (!picked) return 'The night you leave is the first night off. Now tap the day you get back.'

  // Only said when it differs. Every night of the week is the usual case, and
  // "3 nights off, all 3 of them nights the bed would have run" is noise.
  const usual = scheduledNightsAway(schedule, picked)
  const which =
    usual === picked.nights
      ? ''
      : `, and the bed would usually have run on ${usual === 0 ? 'none' : usual} of them`
  return (
    `${nightsOff(picked.nights)}${which}. The night you leave is the first one off, and the ` +
    'night you get back runs as usual.'
  )
}
