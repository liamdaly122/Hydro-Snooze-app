import { Card } from '../components/Card'
import { BedChart } from '../components/BedChart'
import { PowerChart } from '../components/PowerChart'
import { EventLog } from '../components/EventLog'
import { Trends } from '../components/Trends'
import type { ApiClient } from '../api/client'
import type { DeviceEvent, PowerSample } from '../types'

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
      <Card label="Bed, 24 hours">
        <BedChart samples={power} />
      </Card>
      <Card label="Power, 24 hours">
        <PowerChart samples={power} />
      </Card>
      <Card label="Events">
        <EventLog events={events} />
      </Card>
    </>
  )
}
