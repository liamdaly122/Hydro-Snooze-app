import type { Suggestion } from '../types'
import { Fold } from './Fold'
import { Sparkle } from './Icons'
import { InfoButton } from './InfoButton'

/**
 * Tonight's suggested Deep and REM, from the scoreboard (suggest.py).
 *
 * Two places. On Home, in the evening, a card to take it or leave it: that is
 * where somebody is at ten at night. On the Autopilot screen, a folded card with
 * where tonight's is up to and how far the suggestions may go.
 *
 * Taking one changes tonight only, through the same tonight-only change as the
 * temperature card, so the Tonight only line above the bed says what moved and
 * Back to usual undoes it. Nothing here can touch the routine.
 */

function Rows({ s }: { s: Suggestion }) {
  return (
    <div className="suggest__rows">
      {s.parts.map((p) => (
        <div key={p.part} className="suggest__row">
          <span className="suggest__part">{p.label}</span>
          <span className="suggest__temp">{p.tonight_c}°</span>
          <span className="suggest__usual">
            {p.tonight_c === p.usual_c ? 'your usual' : `usual ${p.usual_c}°`}
          </span>
          {p.test && <span className="suggest__chip">Test</span>}
        </div>
      ))}
    </div>
  )
}

function Actions({
  onAccept,
  onDecline,
  busy,
}: {
  onAccept: () => void
  onDecline: () => void
  busy: boolean
}) {
  return (
    <div className="suggest__actions">
      <button type="button" className="sheet__btn" disabled={busy} onClick={onDecline}>
        Not tonight
      </button>
      <button type="button" className="sheet__btn sheet__btn--primary" disabled={busy} onClick={onAccept}>
        Use for tonight
      </button>
    </div>
  )
}

/** On Home, only while there is something to answer. */
export function SuggestionCard({
  suggestion,
  onAccept,
  onDecline,
  busy = false,
}: {
  suggestion: Suggestion
  onAccept: () => void
  onDecline: () => void
  busy?: boolean
}) {
  if (suggestion.state !== 'ready') return null
  return (
    <section className="card suggest">
      <p className="suggest__label">
        <Sparkle size={13} /> Autopilot suggests for tonight
      </p>
      <Rows s={suggestion} />
      {suggestion.why && <p className="suggest__why">{suggestion.why}</p>}
      <Actions onAccept={onAccept} onDecline={onDecline} busy={busy} />
    </section>
  )
}

function summary(s: Suggestion): { text: string; ready: boolean } {
  const test = s.parts.find((p) => p.test)
  switch (s.state) {
    case 'ready':
      return { text: 'Ready for tonight', ready: true }
    case 'accepted':
      return {
        text: test ? `Tonight: ${test.label} ${test.tonight_c}°, a test` : 'Tonight: using it',
        ready: false,
      }
    case 'declined':
      return { text: 'Not tonight', ready: false }
    case 'undone':
      return { text: 'Taken, then back to usual', ready: false }
    case 'usual':
      return { text: 'Tonight runs your usual', ready: false }
    case 'by_hand':
      return { text: 'You have set tonight yourself', ready: false }
    case 'skipped':
      return { text: 'Tonight is off', ready: false }
    case 'no_mat':
      return { text: 'Needs the Sleep Analyzer', ready: false }
    case 'closed':
      return { text: 'Opens in the evening', ready: false }
  }
}

/** On the Autopilot screen: tonight's, and the limits. */
export function SuggestionFold({
  suggestion,
  onAccept,
  onDecline,
  onReach,
  busy = false,
}: {
  suggestion: Suggestion
  onAccept: () => void
  onDecline: () => void
  onReach: (reach: number) => void
  busy?: boolean
}) {
  const s = suggestion
  const said = summary(s)

  return (
    <Fold
      id="suggestions"
      label="Evening suggestion"
      summary={<span className={said.ready ? 'fold__summary--ready' : undefined}>{said.text}</span>}
      info={
        <InfoButton title="Evening suggestion">
          <p>
            Each evening Autopilot suggests tonight&apos;s Deep and REM from your scoreboard. It
            pushes for deep sleep and REM together, as long as time awake and time to fall asleep
            do not get worse.
          </p>
          <p>
            Most nights it suggests the best so far, which is your usual until the scoreboard
            shows something clearly better. About one night in {s.test_every} is a test: one part,
            one degree either side, to find out what that does.
          </p>
          <p>
            Using a suggestion changes tonight only. Your usual is never touched, and Back to
            usual on the home screen undoes it.
          </p>
          <p>
            It never goes outside the limits below. They are set around your usual Deep and REM
            when you choose them, and stay put if you change your usual later.
          </p>
          <p>Drift and Wake stay yours: they are about falling asleep and waking up.</p>
        </InfoButton>
      }
    >
      {s.parts.length > 0 ? (
        <>
          <Rows s={s} />
          {s.why && s.state === 'ready' && <p className="suggest__why">{s.why}</p>}
          {s.state === 'ready' && <Actions onAccept={onAccept} onDecline={onDecline} busy={busy} />}
        </>
      ) : (
        <p className="ap-verdict ap-verdict--dim">
          {s.state === 'no_mat'
            ? 'Suggestions need the Sleep Analyzer connected, or a test night would teach nothing.'
            : s.state === 'closed'
              ? "Tonight's suggestion appears in the evening, before the bed starts getting ready."
              : said.text + '.'}
        </p>
      )}

      <div className="suggest__limits">
        <div className="suggest__limits-head">
          <span className="suggest__limits-title">How far it may go</span>
          <span className="suggest__reach" role="group" aria-label="Degrees either side of your usual">
            {Array.from({ length: s.reach_max }, (_, i) => i + 1).map((r) => (
              <button
                key={r}
                type="button"
                className="suggest__reach-btn"
                aria-pressed={s.reach === r}
                disabled={busy}
                onClick={() => onReach(r)}
              >
                {r}°
              </button>
            ))}
          </span>
        </div>
        <p className="suggest__limits-range">
          {s.limits.map((l) => `${l.label} ${l.low_c} to ${l.high_c}°`).join(' · ')}
        </p>
      </div>
    </Fold>
  )
}
