import { useState } from 'react'
import { Sparkle } from './Icons'
import { Toggle } from './Toggle'
import { MODE_LABEL } from '../types'
import type { Learning, LearningSkill, Mode } from '../types'

/**
 * What the bed has taught the app, and how many nights the rest of it needs.
 *
 * The shape is borrowed from fitness apps on purpose, and it earns its place
 * here for a reason those apps do not have: this thing genuinely cannot know
 * anything on its first evening. Infrared goes one way, so every figure in this
 * project starts as a guess and only a measured night turns it into a number.
 * "Two more nights" is not a retention loop dressed up as progress. It is the
 * literal state of the estimate.
 *
 * Which is also why nothing here is dressed up. A locked row says what it is
 * waiting for; an unlocked one says what was measured and what the unit is being
 * sent because of it. No trophies, no streaks, nothing that would make somebody
 * want the number to go up for its own sake.
 */

/** The nearest thing to unlocking, which is the only one worth a headline. */
function nextUp(learning: Learning): { skill: LearningSkill; left: number } | null {
  let best: { skill: LearningSkill; left: number } | null = null
  for (const mode of learning.modes) {
    for (const skill of mode.skills) {
      if (skill.unlocked) continue
      const left = skill.needed - skill.runs
      if (best === null || left < best.left) best = { skill, left }
    }
  }
  return best
}

function Dots({ runs, needed }: { runs: number; needed: number }) {
  return (
    <span className="learn-dots" aria-hidden="true">
      {Array.from({ length: needed }, (_, i) => (
        <span key={i} className={`learn-dot${i < runs ? ' learn-dot--on' : ''}`} />
      ))}
    </span>
  )
}

export function LearningCard({
  learning,
  onSwitch,
  onForget,
  busy = false,
}: {
  learning: Learning
  onSwitch: (on: boolean) => void
  onForget: (mode: Mode) => void
  busy?: boolean
}) {
  // Which mode is one tap from being cleared. Two taps rather than a dialog:
  // starting again throws away nights of measurement, and a button that does
  // that on the first tap is a button somebody eventually hits by accident.
  const [confirming, setConfirming] = useState<Mode | null>(null)

  const next = nextUp(learning)
  const measured = learning.modes.reduce((n, m) => n + m.unlocked, 0)

  return (
    <section className="card learn">
      <header className="card__head">
        <h2 className="card__label">Learning</h2>
        <Toggle
          on={learning.on}
          onChange={onSwitch}
          label="Use what this bed has taught the app"
        />
      </header>

      {/*
        One line at the top, and it is the one thing worth reading if nothing
        else is. Either how far off the next measurement is, or that there is
        nothing left to wait for.
      */}
      <p className="learn-lead">
        {learning.modes.length === 0 ? (
          'Nothing measured yet. The first night the bed gets ready, this starts counting.'
        ) : next ? (
          <>
            <b>
              {next.left} more night{next.left === 1 ? '' : 's'}
            </b>{' '}
            to unlock {next.skill.title.toLowerCase()}.
          </>
        ) : (
          <>
            <b>Everything measured.</b> Nothing here is estimated any more.
          </>
        )}
      </p>

      {!learning.on && measured > 0 && (
        <p className="learn-off">
          Switched off, so tonight is estimated and the temperatures go out exactly as you set
          them. The nights below carry on being measured either way.
        </p>
      )}

      {learning.modes.map((mode) => (
        <div key={mode.mode} className="learn-mode">
          <div className="learn-mode__head">
            <h3 className="learn-mode__name">
              {MODE_LABEL[mode.mode]} &middot; {mode.target_c}&deg;
            </h3>
            {confirming === mode.mode ? (
              <span className="learn-confirm">
                <button
                  type="button"
                  className="learn-confirm__yes"
                  disabled={busy}
                  onClick={() => {
                    onForget(mode.mode)
                    setConfirming(null)
                  }}
                >
                  Set aside
                </button>
                <button
                  type="button"
                  className="learn-confirm__no"
                  onClick={() => setConfirming(null)}
                >
                  Keep
                </button>
              </span>
            ) : (
              mode.unlocked > 0 && (
                <button
                  type="button"
                  className="learn-again"
                  disabled={busy}
                  onClick={() => setConfirming(mode.mode)}
                >
                  Start again
                </button>
              )
            )}
          </div>

          {confirming === mode.mode && (
            <p className="learn-warn">
              This stops the {MODE_LABEL[mode.mode].toLowerCase()} nights counting towards what
              the app has measured, and it goes back to estimating. The nights themselves are
              kept, and Autopilot still has every one of them.
            </p>
          )}

          {mode.skills.map((skill) => (
            <div
              key={skill.key}
              className={`learn-skill${skill.unlocked ? ' learn-skill--on' : ''}`}
            >
              <span className="learn-skill__mark">
                {skill.unlocked ? <Sparkle size={16} /> : <Dots runs={skill.runs} needed={skill.needed} />}
              </span>
              <div className="learn-skill__body">
                <p className="learn-skill__title">
                  {skill.title}
                  <span className="learn-skill__state">
                    {skill.unlocked
                      ? 'Measured'
                      : `${skill.runs} of ${skill.needed} night${skill.needed === 1 ? '' : 's'}`}
                  </span>
                </p>
                <p className="learn-skill__detail">{skill.detail}</p>
              </div>
            </div>
          ))}
        </div>
      ))}

      <p className="footnote">
        Measured on nights the hose probes decided, never on nights the plug timed out. A run the
        plug ended says the unit stopped working; it never says where the bed got to.
      </p>
    </section>
  )
}
