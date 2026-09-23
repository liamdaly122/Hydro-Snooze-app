import { useState } from 'react'
import { StatusStrip } from '../components/StatusStrip'
import type { ApiClient } from '../api/client'
import type { DeviceState } from '../types'

/**
 * What the unit is doing, as far as anything here can tell.
 *
 * The Status card from the home screen, and the note that used to sit at the
 * bottom of it, which belongs with the one reading that proves the Pi is doing
 * its job rather than under a temperature.
 */
export function Status({ client, state }: { client: ApiClient; state: DeviceState }) {
  const [error, setError] = useState<string | null>(null)

  return (
    <>
      <StatusStrip
        state={state}
        onMute={() => {
          setError(null)
          void client.mute().catch((e: Error) => setError(e.message))
        }}
        onRestartBlaster={() => {
          setError(null)
          void client.restartBlaster().catch((e: Error) => setError(e.message))
        }}
      />

      {error && (
        <p className="footnote footnote--error" onClick={() => setError(null)}>
          {error}
        </p>
      )}

      <p className="footnote">
        The app drives each part of the night itself, rather than handing a schedule to the unit.
        That is what allows cooling and heating in the same night. It also means the Pi has to stay
        running: if it stops, the bed stays wherever it was and will not switch itself off.
      </p>
    </>
  )
}
