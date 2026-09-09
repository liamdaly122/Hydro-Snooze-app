import { useState } from 'react'
import type { DeviceHealth, Health } from '../types'

interface Props {
  health: DeviceHealth[]
  connected: boolean
}

/** What each device is called on screen, and what it actually is. */
const LABEL: Record<string, string> = {
  plug: 'Plug',
  blaster: 'Blaster',
  service: 'Service',
  alerts: 'Alerts',
}

const WHAT: Record<string, string> = {
  plug: 'Shelly Plug S',
  blaster: 'XIAO Smart IR Mate',
  service: 'The Pi, or whatever is running this',
  alerts: 'Whether anything is watching',
}

const ORDER = ['service', 'blaster', 'plug', 'alerts']

/**
 * Three things can stop working independently, and two of them fail silently.
 *
 * The plug just stops answering. The blaster falls off the Wi-Fi and nothing
 * notices until a stage boundary hours later, because presses are hours apart.
 * The service is the one the app finds out about immediately, and it is also the
 * only one the service cannot report on itself: a message saying "I am up" can
 * only ever arrive when it is, so silence is the signal and only this end hears
 * it.
 *
 * Amber rather than red for a short silence, because Wi-Fi drops packets and a
 * bar that cried wolf would get ignored, which is worse than not having one.
 *
 * The fourth chip is not a device. It is whether anything would tell you if this
 * stopped working, which belongs beside them because three green dots saying the
 * hardware is fine mean very little at 3am if nothing is watching. It is also the
 * only row here that can be wrong in a way you would never notice, since a
 * notifier that is switched off looks exactly like a quiet night.
 */
export function DeviceBar({ health, connected }: Props) {
  const [open, setOpen] = useState<string | null>(null)

  const devices: DeviceHealth[] = [
    {
      name: 'service',
      health: connected ? 'ok' : 'down',
      detail: connected
        ? 'Connected. Live updates are arriving'
        : 'Cannot reach the service. It may be restarting, or this phone may be off the network',
      last_ok_at: null,
    },
    ...ORDER.slice(1).map(
      (name) =>
        health.find((d) => d.name === name) ?? {
          name,
          health: 'unknown' as Health,
          detail: 'Not checked yet',
          last_ok_at: null,
        },
    ),
  ]

  const shown = devices.find((d) => d.name === open)

  return (
    <div className="devices">
      <div className="devices__row">
        {devices.map((device) => (
          <button
            key={device.name}
            type="button"
            className={`device device--${device.health}${open === device.name ? ' device--open' : ''}`}
            onClick={() => setOpen(open === device.name ? null : device.name)}
            aria-expanded={open === device.name}
            aria-label={`${LABEL[device.name]}: ${device.detail}`}
          >
            <span className="device__dot" />
            {LABEL[device.name]}
          </button>
        ))}
      </div>
      {shown && (
        <p className="devices__detail">
          <strong>{WHAT[shown.name]}.</strong> {shown.detail}
        </p>
      )}
    </div>
  )
}
