import { useState } from 'react'
import { Power } from './Icons'
import type { DeviceState } from '../types'

interface Props {
  power: DeviceState['power']
  onPress: () => Promise<void>
  onError: (message: string | null) => void
}

/**
 * The one control that has to be reachable from every screen.
 *
 * It sends one press of power and stops. Not "turn it on" or "turn it off": the
 * same single code the button on the remote sends, and whatever the unit makes
 * of that is the unit's business.
 *
 * This used to be cleverer. It read the plug, worked out which direction the
 * unit needed to go, sent whatever that took and confirmed it landed, so the
 * button was absolute and safe to repeat. All of which is right for three in the
 * morning, when nobody is watching, and wrong here: someone tapping this is
 * standing in front of the bed and can see the answer for themselves, and what
 * they wanted was a button that does one thing.
 *
 * The colour still reflects what the plug last said, because that is worth
 * knowing. It just no longer decides what the tap does.
 *
 * The scheduled power off at the wake time is untouched. Nobody is looking at
 * seven in the morning, so that one keeps its wake preamble, its pair of presses
 * and its confirmation.
 */
export function PowerButton({ power, onPress, onError }: Props) {
  const [busy, setBusy] = useState(false)

  const label = busy
    ? 'Sending'
    : power === 'on'
      ? 'Send one press of power. The unit is on'
      : power === 'off'
        ? 'Send one press of power. The unit is off'
        : 'Send one press of power'

  async function press() {
    setBusy(true)
    onError(null)
    try {
      await onPress()
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
