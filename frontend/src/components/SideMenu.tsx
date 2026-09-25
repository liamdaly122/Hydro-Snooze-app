import { useEffect, useRef, useState, type ReactNode, type Ref } from 'react'
import { DeviceBar } from './DeviceBar'
import { Bolt, ChevronRight, Clock, Close, Snowflake, Sparkle, Suitcase } from './Icons'
import { formatDay, formatDays, formatWatts, hasOtherTimes, parseDay } from '../domain'
import type { ApiClient } from '../api/client'
import {
  MODE_LABEL,
  type AuthState,
  type AutopilotNight,
  type DeviceHealth,
  type DeviceState,
  type Holiday,
  type Schedule,
  type TonightState,
} from '../types'

/** The screens the menu opens. */
export type MenuView = 'autopilot' | 'alarm' | 'speed' | 'status' | 'holiday'

interface Props {
  open: boolean
  onClose: () => void
  onOpen: (view: MenuView) => void
  client: ApiClient
  health: DeviceHealth[]
  connected: boolean
  /** Null until the service has answered. The chips still show; the rest waits. */
  state: DeviceState | null
  schedule: Schedule | null
  tonight: TonightState | null
  holiday: Holiday | null
  auth: AuthState
  onSignOut: () => Promise<void>
  onSignOutEverywhere: () => Promise<void>
}

/**
 * Everything except the temperature.
 *
 * The home screen used to hold five cards and a bar of device chips, and at
 * night the only one anybody reaches for is the temperature. The rest are here,
 * each a screen of its own, with the one line under each name that answers the
 * question you would have opened it to ask.
 *
 * The device chips are the exception to "a screen of its own". They are shown
 * right here, at the top, because their whole value is being seen at a glance;
 * a status you have to go looking for is one you stop checking. Tapping one
 * still says what is wrong with it, in place.
 */
export function SideMenu({
  open,
  onClose,
  onOpen,
  client,
  health,
  connected,
  state,
  schedule,
  tonight,
  holiday,
  auth,
  onSignOut,
  onSignOutEverywhere,
}: Props) {
  const first = useRef<HTMLButtonElement>(null)
  const [night, setNight] = useState<AutopilotNight | null>(null)

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

  // Asked each time the menu opens rather than held, because it changes once a
  // morning and nothing pushes it.
  useEffect(() => {
    if (!open) return
    let live = true
    void client
      .getAutopilot()
      .then((n) => live && setNight(n))
      .catch(() => live && setNight(null))
    return () => {
      live = false
    }
  }, [open, client])

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

        <div className="drawer__devices">
          <DeviceBar health={health} connected={connected} />
        </div>

        {/*
          Only the chips until the service has answered. A phone that cannot
          reach it is exactly when the Service chip is worth opening this for,
          and there is nothing true to say under the other rows yet.
        */}
        {state && schedule ? (
          <Items
            first={first}
            night={night}
            state={state}
            schedule={schedule}
            tonight={tonight}
            holiday={holiday}
            onOpen={onOpen}
          />
        ) : (
          <p className="drawer__waiting">Waiting for the service to answer.</p>
        )}

        {auth.required && (
          <SignedIn via={auth.via} onSignOut={onSignOut} onSignOutEverywhere={onSignOutEverywhere} />
        )}
      </nav>
    </div>
  )
}

function Items({
  first,
  night,
  state,
  schedule,
  tonight,
  holiday,
  onOpen,
}: {
  first: Ref<HTMLButtonElement>
  night: AutopilotNight | null
  state: DeviceState
  schedule: Schedule
  tonight: TonightState | null
  holiday: Holiday | null
  onOpen: (view: MenuView) => void
}) {
  const running = tonight?.running ?? schedule
  return (
    <>
      <Item
        focusRef={first}
        icon={<Sparkle size={18} glow />}
        label="Autopilot"
        sub={
          night
            ? `${night.adjustments} adjustment${night.adjustments === 1 ? '' : 's'}, ${weekday(night.wake_at)} night`
            : 'No finished night yet'
        }
        onClick={() => onOpen('autopilot')}
      />
      <Item
        icon={<Clock size={18} />}
        label="Alarm"
        sub={alarmLine(running, schedule, tonight)}
        onClick={() => onOpen('alarm')}
      />
      <Item
        icon={<Snowflake size={18} />}
        label="Cooling speed"
        sub={
          tonight?.speed_changed
            ? `${MODE_LABEL[running.cooling_speed]} tonight, usually ${MODE_LABEL[schedule.cooling_speed]}`
            : MODE_LABEL[schedule.cooling_speed]
        }
        onClick={() => onOpen('speed')}
      />
      <Item
        icon={<Bolt size={18} />}
        label="Status"
        sub={statusLine(state)}
        onClick={() => onOpen('status')}
      />
      <Item
        icon={<Suitcase />}
        label="Holiday mode"
        sub={
          holiday
            ? `On, ${formatDay(parseDay(holiday.leaves_on))} to ${formatDay(parseDay(holiday.back_on))}`
            : 'Off'
        }
        on={holiday !== null}
        onClick={() => onOpen('holiday')}
      />
    </>
  )
}

/**
 * Which way this phone is in, and the two ways out.
 *
 * At the bottom and quiet, because nobody signs out of their own bed. It is
 * here for the day a phone goes missing: Sign out every device, from any other
 * one, and the missing phone needs the password again.
 */
function SignedIn({
  via,
  onSignOut,
  onSignOutEverywhere,
}: {
  via: AuthState['via']
  onSignOut: () => Promise<void>
  onSignOutEverywhere: () => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function run(work: () => Promise<void>) {
    setBusy(true)
    setError(null)
    void work()
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false))
  }

  return (
    <div className="drawer__foot">
      <p className="drawer__foot-line">
        Signed in {via === 'tailscale' ? 'through Tailscale' : 'on the home network'}
      </p>
      <div className="drawer__foot-actions">
        <button type="button" className="pill" disabled={busy} onClick={() => run(onSignOut)}>
          Sign out
        </button>
        <button
          type="button"
          className="pill"
          disabled={busy}
          onClick={() => {
            const sure = window.confirm(
              'Sign out every device, this one included? Each will need the password again.',
            )
            if (sure) run(onSignOutEverywhere)
          }}
        >
          Every device
        </button>
      </div>
      {error && <p className="footnote footnote--error">{error}</p>}
    </div>
  )
}

function Item({
  focusRef,
  icon,
  label,
  sub,
  on = false,
  onClick,
}: {
  /** Not `ref`, which React 18 keeps for itself on a function component. */
  focusRef?: Ref<HTMLButtonElement>
  icon: ReactNode
  label: string
  sub: string
  on?: boolean
  onClick: () => void
}) {
  return (
    <button type="button" className="drawer__item" ref={focusRef} onClick={onClick}>
      <span className="drawer__icon">{icon}</span>
      <span className="drawer__text">
        <span className="drawer__label">{label}</span>
        <span className={`drawer__sub${on ? ' drawer__sub--on' : ''}`}>{sub}</span>
      </span>
      <ChevronRight className="drawer__chevron" />
    </button>
  )
}

function weekday(iso: string): string {
  return new Date(iso).toLocaleDateString('en-GB', { weekday: 'long' })
}

/**
 * "06:30, Mon-Fri", with the weekend's own time after it when there is one, or
 * tonight's time first when tonight is different from what this night usually
 * is. A Saturday's 08:30 is Saturday's usual, not a change.
 */
function alarmLine(running: Schedule, usual: Schedule, tonight: TonightState | null): string {
  if (!usual.enabled) return 'Not running automatically'
  if (usual.days_of_week.length === 0) return 'No days selected'
  const thisNight = tonight?.usual_wake_time ?? usual.wake_time
  if (running.wake_time !== thisNight) {
    return `${running.wake_time} tonight, usually ${thisNight}`
  }
  if (hasOtherTimes(usual)) {
    return `${usual.wake_time}, ${formatDays(usual.other_days)} ${usual.other_wake_time}`
  }
  const days = usual.days_of_week.length === 7 ? 'every day' : formatDays(usual.days_of_week)
  return `${usual.wake_time}, ${days}`
}

/** What the plug says, which is the one thing on that screen that is measured. */
function statusLine(state: DeviceState): string {
  if (state.power === 'unknown') return 'Unit state unknown'
  if (state.power === 'off') return 'Unit off'
  return state.observed_power_w === null ? 'Unit on' : `Unit on, ${formatWatts(state.observed_power_w)}`
}
