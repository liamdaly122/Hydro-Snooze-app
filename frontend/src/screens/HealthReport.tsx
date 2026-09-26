import { type ReactNode, useCallback, useEffect, useState } from 'react'
import { BedByStage } from '../components/BedByStage'
import { Hypnogram, STAGE_COLOUR, STAGE_LABEL, STAGE_ORDER } from '../components/Hypnogram'
import { NightNoteCard } from '../components/NightNoteCard'
import { Bed, ChevronRight, Clock, Moon, Sparkle } from '../components/Icons'
import { ScoreGauge } from '../components/ScoreGauge'
import { VerdictPill } from '../components/VerdictPill'
import { WeekStrip } from '../components/WeekStrip'
import { WITHINGS_CONNECT_URL, type ApiClient } from '../api/client'
import type {
  AutopilotNight,
  HealthAgainst,
  HealthNight,
  HealthReport as Report,
  HealthVital,
  WithingsStatus,
} from '../types'

/**
 * The Health Report: last night as the mat under the mattress measured it.
 *
 * Laid out after the Eight Sleep report, top to bottom: the week, the score, three
 * tiles, the stages, the vitals. Every number on it is the mat's, or worked out
 * from what the mat measured, and every word under a number comes from the
 * service. Where something cannot be known yet it says Learning, and how many
 * more nights it needs, rather than showing a number it has not earned.
 *
 * The bed never waits for any of this. If Withings is unreachable the report
 * says so in a line at the bottom and the rest of the app carries on.
 */

const DAY_MS = 24 * 60 * 60 * 1000

function shiftDate(date: string, days: number): string {
  const d = new Date(`${date}T12:00:00`)
  return new Date(d.getTime() + days * DAY_MS).toISOString().slice(0, 10)
}

function longDate(date: string): string {
  return new Date(`${date}T12:00:00`).toLocaleDateString('en-GB', {
    weekday: 'long',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function clockTime(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

/** 7h 34m, with the letters small, the way the reference sets it. */
function Duration({ seconds }: { seconds: number | null }) {
  if (seconds === null) return <>—</>
  const minutes = Math.round(seconds / 60)
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return (
    <>
      {h > 0 && (
        <>
          {h}
          <small>h</small>{' '}
        </>
      )}
      {m}
      <small>m</small>
    </>
  )
}

function plainDuration(seconds: number): string {
  const minutes = Math.round(seconds / 60)
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return h ? `${h}h${m ? ` ${m}m` : ''}` : `${m}m`
}

/**
 * How long Learning has left, which is the useful half of it. Under the pill
 * rather than in it: "3 more nights" does not fit a pill a third of a phone wide,
 * and it ran off the edge of the card when it tried.
 */
function StillLearning({ needed }: { needed?: number }) {
  if (!needed) return null
  return (
    <p className="hr-learning">
      {needed} more night{needed === 1 ? '' : 's'}
    </p>
  )
}

export function HealthReport({
  client,
  onOpenAutopilot,
}: {
  client: ApiClient
  onOpenAutopilot: () => void
}) {
  const [date, setDate] = useState<string | undefined>(undefined)
  const [report, setReport] = useState<Report | null>(null)
  const [nothingYet, setNothingYet] = useState(false)
  const [status, setStatus] = useState<WithingsStatus | null>(null)
  const [recap, setRecap] = useState<AutopilotNight | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)

  const load = useCallback(
    (on?: string) =>
      client
        .getHealthReport(on)
        .then((r) => {
          setReport(r)
          setNothingYet(false)
          return r
        })
        .catch(() => {
          setNothingYet(true)
          return null
        }),
    [client],
  )

  useEffect(() => {
    void load(date)
  }, [load, date])

  // Until the first night arrives, keep asking. Connecting happens on Withings'
  // own site, on a phone usually in a browser sheet over this app, and nothing
  // tells this screen when that is done. Without this it went on offering
  // Connect to somebody who had just connected.
  useEffect(() => {
    if (!nothingYet) return
    const timer = setInterval(() => {
      void client.getWithings().then(setStatus).catch(() => undefined)
      void load(date)
    }, 5000)
    return () => clearInterval(timer)
  }, [nothingYet, client, load, date])

  // And again whenever the app comes back to the front. A phone keeps an app
  // open for days, and the morning's night arrives while it sits there.
  useEffect(() => {
    const back = () => {
      if (document.visibilityState !== 'visible') return
      void client.getWithings().then(setStatus).catch(() => undefined)
      void load(date)
    }
    document.addEventListener('visibilitychange', back)
    return () => document.removeEventListener('visibilitychange', back)
  }, [client, load, date])

  useEffect(() => {
    let live = true
    void client
      .getWithings()
      .then((s) => live && setStatus(s))
      .catch(() => undefined)
    // For the recap card. Last night only, which is the only night the
    // Autopilot report describes.
    void client
      .getAutopilot()
      .then((n) => live && setRecap(n))
      .catch(() => live && setRecap(null))
    return () => {
      live = false
    }
  }, [client])

  /** A week earlier or later, landing on its latest night if it has one. */
  const stepWeek = (by: -1 | 1) => {
    if (!report) return
    const edge = by < 0 ? shiftDate(report.week[0]!.date, -1) : shiftDate(report.week[6]!.date, 7)
    const target = report.latest && edge > report.latest ? report.latest : edge
    if (report.earliest && target < report.earliest) return
    void client
      .getHealthReport(target)
      .then((r) => {
        const nights = r.week.filter((d) => d.has_night)
        const pick = r.night ? target : nights[nights.length - 1]?.date
        setDate(pick ?? target)
      })
      .catch(() => undefined)
  }

  const fetchNow = () => {
    setBusy(true)
    setNote(null)
    void client
      .syncWithings()
      .then((s) => {
        setStatus(s)
        if (!s.asked) setNote('Fetched a few minutes ago. Withings asks for a gap between them.')
        return load(date)
      })
      .catch((e: Error) => setNote(e.message))
      .finally(() => setBusy(false))
  }

  const disconnect = () => {
    if (!window.confirm('Disconnect Withings? The nights already here stay.')) return
    setBusy(true)
    void client
      .disconnectWithings()
      .then(setStatus)
      .catch((e: Error) => setNote(e.message))
      .finally(() => setBusy(false))
  }

  if (nothingYet) return <NothingYet status={status} />
  if (!report) return <p className="empty">Reading your sleep…</p>

  const night = report.night
  const shownDate = night?.wake_on ?? date ?? report.latest ?? ''
  const recapFits = recap && night && recap.wake_at.slice(0, 10) === night.wake_on

  return (
    <div className="hr">
      {status?.needs_reconnect && (
        <a className="hr-banner" href={WITHINGS_CONNECT_URL}>
          Withings has stopped accepting this connection, so new nights are not arriving.
          <strong> Reconnect</strong>
        </a>
      )}

      <header className="hr-head">
        <h2 className="hr-head__title">Health Report</h2>
        <p className="hr-head__date">{longDate(shownDate)}</p>
      </header>

      <WeekStrip
        days={report.week}
        selected={night?.wake_on ?? null}
        onPick={setDate}
        onPreviousWeek={() => stepWeek(-1)}
        onNextWeek={() => stepWeek(1)}
      />

      {/*
        Straight under the week, above the score. It is the one thing on this
        screen that asks something of you, it takes three taps, and in the
        morning nobody scrolls past a hypnogram to find it.
      */}
      {shownDate && <NightNoteCard key={shownDate} client={client} wakeOn={shownDate} />}

      {night ? (
        <Night night={night} />
      ) : (
        <p className="empty">No night from the mat for this morning.</p>
      )}

      {recapFits && (
        <button type="button" className="card hr-recap" onClick={onOpenAutopilot}>
          <span className="hr-recap__label">
            <Sparkle size={13} /> Autopilot recap
          </span>
          <span className="hr-recap__text">
            While you were asleep, Autopilot made{' '}
            {recap.adjustments === 0
              ? 'no adjustments'
              : `${recap.adjustments} adjustment${recap.adjustments === 1 ? '' : 's'}`}{' '}
            to temperature.
          </span>
          <ChevronRight className="card__chevron hr-recap__chevron" />
        </button>
      )}

      <Source status={status} busy={busy} note={note} onFetch={fetchNow} onDisconnect={disconnect} />
    </div>
  )
}

function Night({ night }: { night: HealthNight }) {
  const { tiles } = night
  return (
    <>
      <section className="hr-score">
        <h3 className="hr-score__title">Sleep Score</h3>
        <ScoreGauge value={night.score.value} verdict={night.score.verdict} label={night.score.label} />
      </section>

      <div className="hr-tiles">
        <Tile
          icon={<Moon size={14} />}
          name="Quality"
          value={tiles.quality.percent === null ? '—' : <>{tiles.quality.percent}<small>%</small></>}
          verdict={tiles.quality}
        />
        <Tile
          icon={<Bed size={15} />}
          name="Routine"
          value={tiles.routine.percent === null ? '—' : <>{tiles.routine.percent}<small>%</small></>}
          verdict={tiles.routine}
        />
        <Tile
          icon={<Clock size={14} />}
          name="Time slept"
          value={<Duration seconds={tiles.time_slept.seconds} />}
          verdict={tiles.time_slept}
        />
      </div>

      <h3 className="hr-section">
        <span>
          <Moon size={17} /> Quality
        </span>
        <span>{tiles.quality.percent === null ? '—' : `${tiles.quality.percent}%`}</span>
      </h3>

      <section className="card hr-card">
        <h3 className="hr-card__title">Sleep stages</h3>
        <div className="hr-legend">
          {STAGE_ORDER.map((s) => (
            <span key={s} className="hr-legend__item">
              <span className="hr-legend__swatch" style={{ background: STAGE_COLOUR[s] }} />
              {STAGE_LABEL[s]}
            </span>
          ))}
          {night.out_of_bed.length > 0 && (
            <span className="hr-legend__item">
              <span className="hr-legend__swatch hr-legend__swatch--out" />
              Out of bed
            </span>
          )}
        </div>

        <Hypnogram
          stages={night.stages}
          outOfBed={night.out_of_bed}
          startsAt={night.in_bed.starts_at}
          endsAt={night.in_bed.ends_at}
        />

        <div className="hr-ends">
          <p>
            <strong>{clockTime(night.fell_asleep_at)}</strong>
            <span>Fell asleep</span>
          </p>
          <p className="hr-ends__right">
            <strong>{clockTime(night.woke_up_at)}</strong>
            <span>Woke up</span>
          </p>
        </div>

        <StageRow name="REM sleep" colour={STAGE_COLOUR.rem} against={night.rem} />
        <StageRow name="Deep sleep" colour={STAGE_COLOUR.deep} against={night.deep} />
      </section>

      <section className="card hr-card">
        <h3 className="hr-card__title">Bed temperature</h3>
        <p className="hr-card__sub">What the bed was doing in each stage of your sleep.</p>
        <BedByStage
          bed={night.bed}
          stages={night.stages}
          outOfBed={night.out_of_bed}
          endsAt={night.in_bed.ends_at}
        />
      </section>

      <section className="card hr-card">
        <h3 className="hr-card__title">Health metrics</h3>
        <div className="hr-metrics">
          <Metric name="Heart rate" vital={night.vitals.heart_rate} />
          <Metric name="HRV" vital={night.vitals.hrv} />
          <Metric name="Breath rate" vital={night.vitals.breath_rate} />
        </div>
      </section>
    </>
  )
}

function Tile({
  icon,
  name,
  value,
  verdict,
}: {
  icon: ReactNode
  name: string
  value: ReactNode
  verdict: { verdict: HealthVital['verdict']; label: string | null; nights_needed?: number }
}) {
  return (
    <div className="hr-tile">
      <p className="hr-tile__name">
        {icon}
        {name}
      </p>
      <p className="hr-tile__value">{value}</p>
      {verdict.label && <VerdictPill verdict={verdict.verdict} label={verdict.label} />}
      <StillLearning needed={verdict.nights_needed} />
    </div>
  )
}

function StageRow({
  name,
  colour,
  against,
}: {
  name: string
  colour: string
  against: HealthAgainst
}) {
  const share = against.percent ?? 0
  const target = plainDuration(against.target_seconds)
  return (
    <div className="hr-stage">
      <div className="hr-stage__bar">
        <span
          className="hr-stage__fill"
          style={{ width: `${Math.max(share, 2)}%`, background: colour }}
        />
        <span className="hr-stage__percent">{against.percent === null ? '—' : `${share}%`}</span>
      </div>
      <div className="hr-stage__text">
        <span className="hr-stage__name">{name}</span>
        <span className="hr-stage__time">
          <Duration seconds={against.seconds} />
        </span>
      </div>
      <VerdictPill
        tone={against.met ? 'good' : 'low'}
        label={`${against.met ? 'Over' : 'Under'} ${target}`}
      />
    </div>
  )
}

function Metric({ name, vital }: { name: string; vital: HealthVital }) {
  const value =
    vital.value === null ? '—' : vital.unit === '/min' ? vital.value.toFixed(1) : vital.value
  return (
    <div className="hr-metric">
      <p className="hr-metric__name">{name}</p>
      <p className="hr-metric__value">
        {value} <small>{vital.unit}</small>
      </p>
      {vital.label && <VerdictPill verdict={vital.verdict} label={vital.label} />}
      <StillLearning needed={vital.nights_needed} />
      {vital.range && (
        <p className="hr-metric__range">
          Usual {vital.range[0]}–{vital.range[1]}
        </p>
      )}
    </div>
  )
}

/** Where the numbers come from, and the two things you can do about it. */
function Source({
  status,
  busy,
  note,
  onFetch,
  onDisconnect,
}: {
  status: WithingsStatus | null
  busy: boolean
  note: string | null
  onFetch: () => void
  onDisconnect: () => void
}) {
  if (!status) return null
  return (
    <section className="card hr-card hr-source">
      <h3 className="hr-card__title">Withings</h3>
      <p className="hr-source__line">
        {status.connected
          ? status.last_sync_at
            ? `From the Sleep Analyzer under the mattress. Last fetched at ${clockTime(status.last_sync_at)}.`
            : 'From the Sleep Analyzer under the mattress. Fetched every half hour.'
          : status.configured
            ? 'Disconnected. The nights already here stay, and no new ones arrive.'
            : 'Withings is not set up on this machine, so no new nights arrive. The ones already here stay.'}
      </p>
      {status.waiting_for_clock && (
        <p className="hr-source__line">Waiting for this machine to learn the time before asking.</p>
      )}
      {(note ?? status.last_error) && (
        <p className="hr-source__line hr-source__line--warn">{note ?? status.last_error}</p>
      )}
      {status.connected ? (
        <div className="pills">
          <button type="button" className="pill" disabled={busy} onClick={onFetch}>
            Fetch now
          </button>
          <button type="button" className="pill" disabled={busy} onClick={onDisconnect}>
            Disconnect
          </button>
        </div>
      ) : (
        status.configured && (
          <div className="pills">
            <a className="pill pill--wide" href={WITHINGS_CONNECT_URL}>
              Connect Withings
            </a>
          </div>
        )
      )}
    </section>
  )
}

/** Before the first night: set up, connect, or wait, whichever is next. */
function NothingYet({ status }: { status: WithingsStatus | null }) {
  let title = 'Nothing from the mat yet'
  let body: string
  let connect = false
  if (!status) {
    body = 'Asking the service where Withings is up to.'
  } else if (!status.configured) {
    title = 'Withings is not set up'
    body =
      'Put HS_WITHINGS_CLIENT_ID and HS_WITHINGS_CLIENT_SECRET in the .env on this machine, restart it, and come back here to connect.'
  } else if (!status.connected) {
    title = 'Connect the Sleep Analyzer'
    body =
      'Sign in to Withings once and the last month of sleep arrives in a minute or so. After that, every night turns up a couple of minutes after you get up.'
    connect = true
  } else {
    body = status.last_error
      ? status.last_error
      : 'Connected. The first nights are on their way, and turn up here within a minute or so.'
  }
  return (
    <div className="hr">
      <header className="hr-head">
        <h2 className="hr-head__title">Health Report</h2>
      </header>
      <section className="card hr-card hr-empty">
        <Moon size={28} />
        <h3 className="hr-card__title">{title}</h3>
        <p className="hr-source__line">{body}</p>
        {status?.needs_reconnect && (
          <p className="hr-source__line hr-source__line--warn">
            Withings has stopped accepting this connection.
          </p>
        )}
        {(connect || status?.needs_reconnect) && (
          <div className="pills">
            <a className="pill pill--wide pill--connect" href={WITHINGS_CONNECT_URL}>
              {status?.needs_reconnect ? 'Reconnect Withings' : 'Connect Withings'}
            </a>
          </div>
        )}
      </section>
    </div>
  )
}
