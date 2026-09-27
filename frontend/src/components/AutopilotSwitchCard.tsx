import type { AutopilotSwitch } from '../types'
import { InfoButton } from './InfoButton'
import { Toggle } from './Toggle'

/**
 * The switch over all of Autopilot, at the top of its screen, and Stay on
 * target inside it.
 *
 * On, it learns the bed's timings and corrections, trims the setting through
 * the night when the bed sits off the number, switches to the quieter mode
 * mid-part when the bed allows, and suggests tonight's Deep and REM. Off, the
 * bed runs exactly the temperatures set, at the times set, and tonight goes
 * back to usual if it was running a suggestion. The reports stay: every night
 * is still written down, so turning it back on loses nothing.
 *
 * Stay on target is the trade between silence and accuracy: warming mode is
 * loud and cooling is silent. Off, a warm part goes quiet once the bed reaches
 * the number. On, every part stays in the mode it started in, all night. It
 * replaced three Hold levels (Quiet, Balanced, Close) that on the real bed did
 * not change much. See backend/hydrosnooze/hold.py.
 */
export function AutopilotSwitchCard({
  state,
  onSwitch,
  onStay,
  busy = false,
}: {
  state: AutopilotSwitch
  onSwitch: (on: boolean) => void
  onStay: (on: boolean) => void
  busy?: boolean
}) {
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
            It hands warm parts to the silent cooling mode once the bed reaches the number, unless
            Stay on target below is on. And each evening it picks tonight&apos;s Deep and REM and
            sets them itself, now and then trying a degree either side to learn what suits you.
            Back to usual on the home screen undoes one night.
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
          <span className="ap-hold__title">Stay on target</span>
          <InfoButton title="Stay on target">
            <p>
              Each part of the night starts in the mode its direction calls for. A part that is
              warmer than the one before it warms, and a part that is cooler cools.
            </p>
            <p>
              Off, a warm part switches to the silent cooling mode once the bed reaches the number,
              and warms again if the bed falls a degree below. Quieter, but the bed swings about a
              degree under what you asked for.
            </p>
            <p>
              On, every part stays in the mode it started in, all night. The bed stays at the
              number, and warm parts are as loud as warming mode is. If the bed still sits off the
              number for half an hour, Autopilot sends a degree more or less, the same as always.
            </p>
            <p>
              Changing the temperature part way through, from the app or the bedside, counts as a
              direction too. Cooler switches that part to cooling and warmer to warming, and it
              stays that way.
            </p>
            <p>
              It needs Autopilot on. With Autopilot off, every part stays in the mode the schedule
              gave it anyway, but nothing adjusts the setting.
            </p>
          </InfoButton>
          <Toggle
            on={state.stay_on_target}
            onChange={(next) => !busy && onStay(next)}
            label="Stay on target"
            disabled={busy || !state.on}
          />
        </div>
        <p className="ap-hold__describe">
          {!state.on
            ? 'Off with Autopilot: every part stays in the mode the schedule gave it.'
            : state.stay_on_target
              ? 'On. Every part stays warming or cooling all night, so the bed holds the number. Louder on warm parts.'
              : 'Off. Warm parts go quiet once the bed reaches the number, and warm again a degree below.'}
        </p>
      </div>
    </section>
  )
}
