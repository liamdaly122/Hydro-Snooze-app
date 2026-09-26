import type { AutopilotSwitch, HoldName } from '../types'
import { InfoButton } from './InfoButton'
import { Toggle } from './Toggle'

/**
 * The switch over all of Autopilot, at the top of its screen, and how closely
 * it holds the bed.
 *
 * On, it learns the bed's timings and corrections, trims the setting through
 * the night when the bed sits off the number, switches to the quieter mode
 * mid-part when the bed allows, and suggests tonight's Deep and REM. Off, the
 * bed runs exactly the temperatures set, at the times set, and tonight goes
 * back to usual if it was running a suggestion. The reports stay: every night
 * is still written down, so turning it back on loses nothing.
 *
 * Hold is the trade between silence and accuracy for warm parts: warming mode
 * is loud and cooling is silent, so how soon a warm part is handed to the quiet
 * mode, and how far the bed may fall before warming comes back, is a choice.
 */
export function AutopilotSwitchCard({
  state,
  onSwitch,
  onHold,
  busy = false,
}: {
  state: AutopilotSwitch
  onSwitch: (on: boolean) => void
  onHold: (hold: HoldName) => void
  busy?: boolean
}) {
  const chosen = state.holds.find((h) => h.name === state.hold)

  return (
    <section className="card ap-switch">
      <div className="ap-switch__row">
        <div className="ap-switch__text">
          <h2 className="card__label">Autopilot</h2>
          <p className="ap-switch__state">
            {state.on
              ? "On. It learns your bed, holds it at the number and picks tonight's Deep and REM."
              : 'Off. The bed runs exactly the temperatures you set, at the times you set.'}
          </p>
        </div>
        <InfoButton title="Autopilot">
          <p>On, Autopilot does five things:</p>
          <p>
            It learns how long your bed takes to get ready and where it settles, and uses that for
            the head start and the temperature it sends. Through the night, if the bed sits more
            than half a degree off the number for half an hour, it sends a degree more or less,
            one step at a time and never more than 4° from what you asked for.
          </p>
          <p>
            It hands warm parts to the silent cooling mode when your body heat can hold them, as
            Hold below sets out. And each evening it picks tonight&apos;s Deep and REM and sets
            them itself, now and then trying a degree either side to learn what suits you. Back to
            usual on the home screen undoes one night.
          </p>
          <p>
            Off, none of that happens. The bed gets exactly the temperatures you set, at the times
            you set, and still gets ready before lights out on the standard estimate. If tonight
            was running Autopilot&apos;s choice, it goes back to your usual.
          </p>
          <p>
            Every night is still written down either way, so the Health Report, Sleep timing and
            the Scoreboard carry on, and turning Autopilot back on loses nothing.
          </p>
        </InfoButton>
        <Toggle on={state.on} onChange={(next) => !busy && onSwitch(next)} label="Autopilot" />
      </div>

      <div className={`ap-hold${state.on ? '' : ' ap-hold--off'}`}>
        <div className="ap-hold__head">
          <span className="ap-hold__title">Hold</span>
          <span className="suggest__reach" role="group" aria-label="How closely warm parts are held">
            {state.holds.map((h) => (
              <button
                key={h.name}
                type="button"
                className="suggest__reach-btn"
                aria-pressed={state.hold === h.name}
                disabled={busy || !state.on}
                onClick={() => onHold(h.name)}
              >
                {h.label}
              </button>
            ))}
          </span>
        </div>
        <p className="ap-hold__describe">
          {state.on ? chosen?.describe : 'Off with Autopilot: warm parts stay in the mode the schedule gave them.'}
        </p>
      </div>
    </section>
  )
}
