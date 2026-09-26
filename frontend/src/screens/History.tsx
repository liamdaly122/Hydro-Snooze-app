import { BedChart } from '../components/BedChart'
import { Fold } from '../components/Fold'
import { PowerChart } from '../components/PowerChart'
import { EventLog } from '../components/EventLog'
import { Trends } from '../components/Trends'
import { homeNow } from '../domain'
import type { ApiClient } from '../api/client'
import type { DeviceEvent, PowerSample } from '../types'

/**
 * Everything that has happened, long view first.
 *
 * Every section folds, because together they made a long screen and most days
 * only one of them is what you came for. Folded, each still says the one thing
 * worth a glance on the line beside its title, and each one stays open or shut
 * the way it was left on this phone. Trends starts open, being the reason the
 * screen is opened most days; the last day's charts and the log start shut.
 */

/** The same rule as the morning report: a reading counts for as long as it
 *  stood, and a gap longer than a few beats counts for one beat, not for power
 *  nothing measured. See report.kwh in the service. */
const LONGEST_BEAT_S = 90

function time(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

function bedLine(samples: PowerSample[]): string {
  const read = samples.filter((s) => s.return_c !== null)
  if (read.length === 0) return 'No readings yet'
  const values = read.map((s) => s.return_c!)
  const last = read[read.length - 1]!
  const low = Math.min(...values).toFixed(1)
  const high = Math.max(...values).toFixed(1)
  return `${last.return_c!.toFixed(1)}° at ${time(last.at)} · ${low} to ${high}°`
}

function powerLine(samples: PowerSample[]): string {
  if (samples.length < 2) return 'No readings yet'
  let joules = 0
  for (let i = 0; i < samples.length - 1; i += 1) {
    const held = (new Date(samples[i + 1]!.at).getTime() - new Date(samples[i]!.at).getTime()) / 1000
    joules += samples[i]!.watts * Math.min(held, LONGEST_BEAT_S)
  }
  return `${(joules / 3_600_000).toFixed(2)} kWh used`
}

function eventsLine(events: DeviceEvent[]): string {
  if (events.length === 0) return 'Nothing logged yet'
  // The newest first, as the log has them.
  const since = homeNow().getTime() - 24 * 60 * 60 * 1000
  const loud = events.filter((e) => e.level !== 'info' && new Date(e.at).getTime() >= since)
  const latest = `Latest at ${time(events[0]!.at)}`
  if (loud.length === 0) return latest
  return `${latest} · ${loud.length} worth a look today`
}

export function History({
  client,
  power,
  events,
}: {
  client: ApiClient
  power: PowerSample[]
  events: DeviceEvent[]
}) {
  return (
    <>
      {/*
        The long view first: weeks and months of nights. The last day, which is
        what this screen was until there were enough nights to make a trend,
        follows it.
      */}
      <Trends client={client} />
      {/*
        The bed first. The power chart below says what the machine did, which is
        what there was to look at for months; this says what happened to the bed,
        which is the thing the machine is for.
      */}
      <Fold id="history-bed" label="Bed, 24 hours" summary={bedLine(power)}>
        <BedChart samples={power} />
      </Fold>
      <Fold id="history-power" label="Power, 24 hours" summary={powerLine(power)}>
        <PowerChart samples={power} />
      </Fold>
      <Fold id="history-events" label="Events" summary={eventsLine(events)}>
        <EventLog events={events} />
      </Fold>
    </>
  )
}
