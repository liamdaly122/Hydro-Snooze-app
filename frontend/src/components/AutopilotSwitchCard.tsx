import { InfoButton } from './InfoButton'
import { Toggle } from './Toggle'

/**
 * The switch over all of Autopilot, at the top of its screen.
 *
 * On, it learns the bed's timings and corrections, switches to the quieter mode
 * mid-part when the bed allows, and suggests tonight's Deep and REM. Off, the
 * bed runs exactly the temperatures set, at the times set, and tonight goes
 * back to usual if it was running a suggestion. The reports stay: every night
 * is still written down, so turning it back on loses nothing.
 */
export function AutopilotSwitchCard({
  on,
  onSwitch,
  busy = false,
}: {
  on: boolean
  onSwitch: (on: boolean) => void
  busy?: boolean
}) {
  return (
    <section className="card ap-switch">
      <div className="ap-switch__row">
        <div className="ap-switch__text">
          <h2 className="card__label">Autopilot</h2>
          <p className="ap-switch__state">
            {on
              ? "On. It learns your bed, holds it steady and suggests tonight's Deep and REM."
              : 'Off. The bed runs exactly the temperatures you set, at the times you set.'}
          </p>
        </div>
        <InfoButton title="Autopilot">
          <p>On, Autopilot does four things:</p>
          <p>
            It learns how long your bed takes to get ready and where it settles, and uses that
            for the head start and the temperature it sends. It switches to the quieter mode
            partway through a part when the bed allows. And it suggests tonight&apos;s Deep and
            REM each evening.
          </p>
          <p>
            Off, none of that happens. The bed gets exactly the temperatures you set, at the times
            you set, and still gets ready before lights out on the standard estimate. If tonight
            was running a suggestion, it goes back to your usual.
          </p>
          <p>
            Every night is still written down either way, so the Health Report, Sleep timing and
            the Scoreboard carry on, and turning Autopilot back on loses nothing. Learning keeps
            its own switch for when Autopilot is on.
          </p>
        </InfoButton>
        <Toggle on={on} onChange={(next) => !busy && onSwitch(next)} label="Autopilot" />
      </div>
    </section>
  )
}
