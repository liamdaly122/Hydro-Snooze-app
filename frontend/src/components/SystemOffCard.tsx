import { useState } from 'react'

/**
 * The home screen while HydroSnooze is switched off.
 *
 * In place of the temperature card rather than above it. Every control on that
 * card would be refused, and a screen full of buttons that all say no is worse
 * than one that says why and how to undo it.
 */
export function SystemOffCard({ onTurnOn }: { onTurnOn: () => Promise<unknown> }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  return (
    <section className="card system-off" role="status">
      <h2 className="system-off__title">HydroSnooze is off</h2>
      <p className="system-off__text">
        No night runs and nothing is sent to the unit. The app takes it to be switched off or
        unplugged, so it asks nothing of the plug, the blaster or the probes, and the bedside buttons
        do nothing.
      </p>
      <p className="system-off__text">
        Your schedule and settings are kept. Switched back on during a night, it picks up whatever
        part the night has reached.
      </p>
      <button
        type="button"
        className="pill pill--wide system-off__on"
        disabled={busy}
        onClick={() => {
          setBusy(true)
          setError(null)
          void onTurnOn()
            .catch((e: Error) => setError(e.message))
            .finally(() => setBusy(false))
        }}
      >
        Switch on
      </button>
      {error && <p className="footnote footnote--error">{error}</p>}
    </section>
  )
}
