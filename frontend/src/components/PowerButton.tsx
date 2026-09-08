import { useState } from 'react'
import { Power } from './Icons'
import type { DeviceState } from '../types'

interface Props {
  power: DeviceState['power']
  onOn: () => Promise<void>
  onOff: () => Promise<void>
  onError: (message: string | null) => void
}

/**
 * The one control that has to be reachable from every screen.
 *
 * The remote's power button is a toggle, and a toggle is the wrong shape for a
 * unit that cannot be read back: press it when the app has guessed wrong and you
 * get the opposite of what you asked for. The service does not work that way.
 * `power_on` and `power_off` each read the plug first, return early if the unit
 * is already there, and press again if it is not, so both are absolute and both
 * are safe to repeat.
 *
 * That leaves one question: what should this do when the state is `unknown`,
 * which over infrared happens often. It turns the unit OFF. Off is the safe
 * direction, because a bed left heating unattended is the bad outcome and a unit
 * already off ignores the request. It also resolves the unknown, since powering
 * off is verified against the plug, so one tap always ends somewhere known.
 */
export function PowerButton({ power, onOn, onOff, onError }: Props) {
  const [busy, setBusy] = useState(false)

  const turningOff = power !== 'off'
  const label = busy
    ? 'Working, this takes a few seconds'
    : power === 'on'
      ? 'Turn the unit off'
      : power === 'off'
        ? 'Turn the unit on'
        : 'The unit\'s state is unknown. This turns it off'

  async function press() {
    setBusy(true)
    onError(null)
    try {
      await (turningOff ? onOff() : onOn())
    } catch (e) {
      onError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <button
      type="button"
      className={`power power--${power}${busy ? ' power--busy' : ''}`}
      onClick={() => void press()}
      disabled={busy}
      aria-label={label}
      title={label}
    >
      <Power />
    </button>
  )
}
