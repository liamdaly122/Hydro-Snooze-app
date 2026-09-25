import type { ScorePart, ScoreSetting, Scoreboard } from '../types'
import { Fold } from './Fold'
import { InfoButton } from './InfoButton'

/**
 * Each temperature each part of the night has run at, and the sleep on those
 * nights: deep sleep and REM together for Deep and for REM, which is what
 * Autopilot pushes for, and time to fall asleep for Drift.
 *
 * Folded, one line: how many nights are recorded and whether anything is ahead
 * yet. Open, a row per temperature with its average and how many nights it has.
 *
 * Every verdict is the service's (withings/scoreboard.py). The one this card
 * says most, for months, is "not sure yet", because that is what the numbers
 * say until a gap is bigger than the swing between ordinary nights. Nothing
 * here is dressed up as a result before it is one.
 */

function minutes(seconds: number): string {
  const m = Math.round(seconds / 60)
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`
}

/** Wake is scored on the morning's rating, one to five, not on anything timed. */
function rated(p: ScorePart): boolean {
  return p.unit === 'rating'
}

/** A score in whatever the part is counted in. */
function amount(p: ScorePart, value: number): string {
  return rated(p) ? `${value.toFixed(1)} of 5` : minutes(value)
}

/** A gap between two scores: "12m", or "0.8 points". */
function gap(p: ScorePart, value: number): string {
  return rated(p) ? `${value.toFixed(1)} point${value === 1 ? '' : 's'}` : minutes(value)
}

/** "said too warm on 2 of 5", when the mornings said anything either way. */
function feltLine(s: ScoreSetting): string {
  const f = s.felt
  if (!f || f.answered === 0) return ''
  const said = [
    f.too_warm ? `too warm on ${f.too_warm}` : '',
    f.too_cold ? `too cold on ${f.too_cold}` : '',
  ].filter(Boolean)
  return said.length ? ` · felt ${said.join(', ')} of ${f.answered}` : ` · felt right on all ${f.answered}`
}

/** "Deep sleep" to "deep sleep" mid-sentence, leaving REM as it is. */
function inSentence(measure: string): string {
  return measure.startsWith('REM') ? measure : measure[0]!.toLowerCase() + measure.slice(1)
}

function summary(b: Scoreboard): string {
  if (b.recorded === 0) return 'Recording starts tonight'
  if (b.nights === 0) return 'Waiting for the mat'
  const clear = b.parts.find((p) => p.verdict === 'clear')
  if (clear) return `${clear.label}: ${clear.leader_c}° is ahead`
  const nights = `${b.nights} night${b.nights === 1 ? '' : 's'}`
  if (b.tests === 0) return `${nights} recorded, no tests yet`
  return `${b.tests} test night${b.tests === 1 ? '' : 's'}, not sure yet`
}

function verdict(p: ScorePart, needs: number): string {
  const measure = inSentence(p.measure)
  if (rated(p)) {
    switch (p.verdict) {
      case 'empty':
        return 'Rate how waking up felt on the Health Report and Wake is scored here.'
      case 'one_setting':
        return `Only ${p.settings[0]!.set_c}° so far. A few mornings at another Wake temperature give it something to compare with.`
      case 'not_sure':
        return p.leader_c === null
          ? `Not sure yet. Each temperature needs ${needs} rated mornings before it is compared.`
          : `${p.leader_c}° is rated ${gap(p, p.gap_s!)} higher, but ordinary mornings vary by up to ${gap(p, p.swing_s!)}. Not sure yet.`
      case 'clear':
        return `Mornings after ${p.leader_c}° are rated about ${gap(p, p.gap_s!)} higher than after ${p.runner_c}°, more than ordinary mornings vary by.`
    }
  }
  switch (p.verdict) {
    case 'empty':
      return 'Nothing recorded for this part yet.'
    case 'one_setting':
      return `Only ${p.settings[0]!.set_c}° tried so far. The evening suggestions will try a degree either side.`
    case 'not_sure':
      return p.leader_c === null
        ? `Not sure yet. Each temperature needs ${needs} nights before it is compared.`
        : `${p.leader_c}° is ahead by ${minutes(p.gap_s!)}, but ordinary nights swing by up to ${minutes(p.swing_s!)}. Not sure yet.`
    case 'clear':
      return p.more_is_better
        ? `${p.leader_c}° gives you about ${minutes(p.gap_s!)} more ${measure} than ${p.runner_c}°, more than ordinary nights swing by.`
        : `${p.leader_c}° gets you to sleep about ${minutes(p.gap_s!)} faster than ${p.runner_c}°, more than ordinary nights swing by.`
  }
}

function Row({
  p,
  s,
  widest,
  leads,
  needs,
  split,
}: {
  p: ScorePart
  s: ScoreSetting
  widest: number
  leads: boolean
  needs: number
  /** Show deep sleep and REM separately under the total. */
  split: boolean
}) {
  const few = s.nights < needs
  return (
    <div className={`score-row${leads ? ' score-row--leads' : ''}${few ? ' score-row--few' : ''}`}>
      <span className="score-row__temp">{s.set_c}°</span>
      <span className="score-row__middle">
        <span className="score-row__track">
          <span
            className="score-row__fill"
            style={{ width: `${Math.max(4, (s.mean_s / Math.max(1, widest)) * 100)}%` }}
          />
        </span>
        <span className="score-row__note">
          {s.nights} night{s.nights === 1 ? '' : 's'}
          {s.room_c !== null ? ` · room ${s.room_c.toFixed(1)}°` : ''}
          {feltLine(s)}
        </span>
        {split && s.deep_s !== null && s.rem_s !== null && (
          <span className="score-row__note">
            deep {minutes(s.deep_s)} · REM {minutes(s.rem_s)}
          </span>
        )}
      </span>
      <span className="score-row__value">{amount(p, s.mean_s)}</span>
    </div>
  )
}

export function ScoreboardCard({ board }: { board: Scoreboard }) {
  const shown = board.parts.filter((p) => p.verdict !== 'empty')

  return (
    <Fold
      id="scoreboard"
      label="Scoreboard"
      summary={summary(board)}
      info={
        <InfoButton title="Scoreboard">
          <p>
            Every morning the Pi writes down what each part of the night was set to, read from
            what the bed was actually asked for, and sets it beside what the mat measured.
          </p>
          <p>
            Deep and REM are both scored on deep sleep and REM added together, because that is
            what Autopilot pushes for: more deep sleep bought with less REM is not a win. The
            split is shown under each setting. Drift is scored on how long you took to fall
            asleep. Wake is scored on how waking up felt, from the rating you give it on the
            Health Report, because nothing the mat measures says that.
          </p>
          <p>
            A part you changed by hand, or whose setting was not held for most of it, does not
            count. Neither does a night the mat missed, or one under three hours asleep, or one
            tagged Alcohol, Ill or Someone else in the bed: those move sleep by more than a
            degree on the bed does.
          </p>
          <p>
            Two temperatures are only called apart when the gap between them is bigger than
            ordinary nights swing by on their own, and each needs {board.setting_needs} nights
            first. Until then it says not sure yet, which is the honest answer for a while.
          </p>
          <p>
            It shows what went with what, not what caused it. The room temperature beside each
            setting is there so a warm week cannot pass for a good temperature.
          </p>
        </InfoButton>
      }
    >
      {shown.length === 0 ? (
        <p className="ap-verdict ap-verdict--dim">
          {board.recorded === 0
            ? 'The first night is written down the morning after it runs.'
            : 'Nights are being written down. They appear here once the mat has them too.'}
        </p>
      ) : (
        shown.map((p) => {
          const widest = rated(p) ? 5 : Math.max(...p.settings.map((s) => s.mean_s))
          return (
            <div key={p.part} className="score-part">
              <h3 className="score-part__head">
                {p.label}
                <span className="score-part__measure">scored on {inSentence(p.measure)}</span>
              </h3>
              {p.settings.map((s) => (
                <Row
                  key={s.set_c}
                  p={p}
                  s={s}
                  widest={widest}
                  leads={p.verdict === 'clear' && s.set_c === p.leader_c}
                  needs={board.setting_needs}
                  split={p.part === 'deep' || p.part === 'rem'}
                />
              ))}
              <p className="score-part__verdict">{verdict(p, board.setting_needs)}</p>
            </div>
          )
        })
      )}

      <p className="score-foot">
        {board.nights} night{board.nights === 1 ? '' : 's'} with the mat in the last{' '}
        {board.window_days} days
        {board.tests > 0 ? `, ${board.tests} of them tests` : ''}.
        {board.left_out && board.left_out.nights > 0 && (
          <>
            {' '}
            {board.left_out.nights} more left out:{' '}
            {Object.entries(board.left_out.by_tag)
              .map(([tag, n]) => `${tag.toLowerCase()} ${n}`)
              .join(', ')}
            .
          </>
        )}
      </p>
    </Fold>
  )
}
