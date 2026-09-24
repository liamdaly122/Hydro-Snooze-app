# HydroSnooze

Local control for my HydroSnooze HS1001 bed cooler. An app on the iPhone Home Screen that sets the
temperature for each part of the night and the time I want to wake up, then does the rest by itself.

No subscription, no cloud, no account. It runs on a small computer in the house and talks to the
unit through an infrared blaster and a smart plug.

## Where this is up to

*Last updated 16 September 2026.*

**It runs the bed.** A Raspberry Pi in the house drives a real HS1001 through a
real infrared blaster, watches a real smart plug, and reads three real temperature
probes taped to the mattress. It has run whole nights unattended and survived ten
deliberate failures on the real hardware, which is recorded in `CHECKLIST.md`
rather than claimed here.

The night has four stages: **Drift, Deep, REM and Wake.** Drift is a warmer half
hour at the front, so getting into bed is not getting into the cold.

**The app has started learning.** The plug is the only real sensor on the unit
itself, so the app compares what it asked for against what the bed actually did
and corrects the difference. The Autopilot screen shows what it knows and how many
more nights it needs before it trusts itself.

**Next: a Withings Sleep Analyzer.** Everything above measures the machine. Nothing
measures the sleeper. The mat goes under the mattress and its data gets joined
against the mattress temperature, so for the first time the project can ask whether
the temperature it chose was any good. See [docs/withings.md](docs/withings.md) for
what the API actually does, established against the live API rather than read.

**[CHECKLIST.md](CHECKLIST.md) is the thing to work down.** `ROADMAP.md` has the
reasoning behind it and `SETUP.md` has the detail for each hardware step.

## Running it

One command. It sets up Python, installs what it needs, builds the app, and
starts the service.

```sh
./scripts/dev.sh
```

**Never used Terminal before?** `GETTING-STARTED.md` walks through the whole
thing from nothing, including getting the code onto the Mac in the first place.

Then open `http://localhost:8000`, or the address it prints for the phone, which
works from anything on the same Wi-Fi.

Every button press the service would have sent is printed in the terminal as it
happens. That press log is the most useful debugging tool in this project.

### Watching an evening without waiting for one

The app has a third tab, a spanner, which only appears when the service is
talking to a simulated unit. It has a clock that can be jumped.

Jump to 21:29, set the speed to 60x, and the whole evening plays out in about
half a minute: the unit powers on, rails down and counts up to the Drift
temperature, then steps through Deep, REM and Wake at their boundaries and
switches off in the morning.

### Running the tests

```sh
cd backend && . .venv/bin/activate && pytest
```

643 of them. The ones that matter check that every temperature sequence lands on
exactly the right number from every plausible starting state, that a stage
boundary still fires when everything else is broken, and that a night is never
scored against a target nobody wrote down.

## The idea

The unit has no clock, no Wi-Fi and no way to be read back.

It does have its own Smart Sleep Schedule: three phases of 4h, 4h and 30m, armed with two presses
and then left to run itself. This project used to drive that, and it was elegant. It is also a
straitjacket. While it runs, the unit refuses to change temperature and refuses to switch between
cooling and warming. Since a cooler cannot warm a bed, that capped every night at "somewhere at or
below the bedroom temperature".

So the app drives the night itself. It powers the unit on, sets a temperature, and comes back at
each stage boundary to set another. Outside the unit's own schedule everything is unlocked, which
means any number of stages, any durations, and heating and cooling in the same night. Tonight that
is four stages: Drift, Deep, REM and Wake.

Two things make that work:

1. **Every temperature is forced rather than tracked.** Press down 25 times to pin the unit at the
   mode's minimum, then count up to the target. Idempotent from any starting state, including after
   I have used the physical remote.
2. **Every command starts with two throwaway presses.** The display goes dark after five minutes and
   swallows the first press waking up. Stage boundaries are hours apart, so it is always dark.

**What it costs.** The unit no longer switches itself off, so the app must, and the Pi has to stay
running all night. If it dies at 3am the bed stays where it was. The Shelly's own auto-off timer is
no longer a nicety, it is the last line of defence.

## Layout

```
backend/hydrosnooze/
  models.py            modes, ranges, rail counts, the four stages, the night plan
  clock.py             real, simulated and virtual time
  clocksync.py         whether the Pi's clock can be believed yet
  sequences.py         the button recipes: rail and count, the wake preamble
  scheduler.py         what should be happening, and when it is too late to bother
  service.py           everything wired together, and the only place holding state
  db.py                schedule, profiles, events, power history, what it has learned
  autopilot.py         how close last night was to what was asked for
  report.py            the morning summary
  events.py            the log the app reads
  notify.py            push, when something needs saying
  watchdog.py          the last line of defence
  pi.py                temperature, throttling, disk, uptime
  ircodes.py           the eight captured codes
  config.py            every setting, and which adapters to use
  main.py              the web service and the background loops
  api/                 routes, schemas, and the dev-only time machine
  withings/            the Sleep Analyzer: client, parser, the loop, the Health Report
  adapters/
    fake_unit.py       a simulated HS1001 with all the documented quirks
    fake_transmitter.py  prints every press instead of sending it
    esphome.py         the real infrared blaster
    shelly.py          the real plug
    probes.py          the three DS18B20s on the mattress
frontend/src/
  types.ts             the same vocabulary, mirrored for the app
  api/                 the client interface, the live client, and seed data
  screens/Home.tsx     the bed and the temperature card, and nothing else that is always there
  components/PodHero.tsx  the bed in 3D, glowing with whatever the plug says the unit is doing
  pod/scene.ts         the 3D scene itself: the mattress, the pillows and the studio lights
  components/SideMenu.tsx  everything else: the device chips, and a way into each screen below
  screens/HealthReport.tsx  last night off the Sleep Analyzer: the score, the stages, the vitals
  screens/Autopilot.tsx  what it has learned, and how last night went
  screens/Alarm.tsx    the wake time, and the way into the whole schedule
  screens/CoolingSpeed.tsx  the cooling speed, now or for tonight
  screens/Status.tsx   what the unit is doing, as far as anything can tell
  screens/History.tsx  power, temperature and the event log
  screens/Holiday.tsx  holiday mode: the day I leave and the day I am back
  screens/Dev.tsx      the time machine and the press log
scripts/dev.sh         run the whole thing on this machine
scripts/deploy.sh      copy the working tree to the Pi
scripts/install.sh     set the Pi up from nothing
docs/withings.md       what the Withings API actually does
docs/temperature-probes.md  the three probes, from parts to readings
```

## Putting it on a phone while the design is being settled

This was for settling the design before the Pi existed, and it is kept because it is still the
quickest way to look at a screen on the phone without anything else switched on. Pointed at seed
data rather than the service, the app is a plain static site and can be hosted anywhere. There is a
`vercel.json` at the root for exactly this: point Vercel at this repository and it builds
`frontend/` and gives me a URL.

Vercel scans the repository, sees `backend/pyproject.toml`, and decides this is a two-part app with
a website and a FastAPI service. It then refuses to deploy until it is told how to handle both. That
is the wrong shape here: the service does not live on Vercel and never will.

Either answer works, and both are in the repository:

- **Set Root Directory to `frontend`** in the Vercel project settings. Vercel then only ever looks
  inside that folder, sees one Vite app, and reads `frontend/vercel.json`. This is the one to
  prefer.
- **Or leave Root Directory at the repository root**, where `vercel.json` declares a single service
  pointing at `frontend/` and routes everything to it.

**This is for the design phase only.** The finished thing cannot live on Vercel, and neither can any
other hosting company. See below.

## Why the finished app runs in the house and not on the internet

The service has to do three things that a hosting company physically cannot:

1. **Shout at the unit in infrared.** The commands leave through a small blaster sitting in the
   bedroom. A server in a data centre has no way to reach a device on my home network.
2. **Be awake at 21:30 with nobody watching.** Hosting like Vercel runs code when somebody loads the
   page and then stops. The whole point of this app is that it fires while I am downstairs or
   asleep, so it needs a process that stays running.
3. **Remember things between commands.** The schedule, the event log and the power history all have
   to survive a restart.

So the service runs on a small always-on computer in the house, and the app is served from there
too. The frontend is still built on my Mac and copied across as static files, which is the same
build Vercel would run.

## Running the tests

```sh
cd backend
uv venv .venv && . .venv/bin/activate
uv pip install -e '.[dev]'
pytest
```

## Design notes

Styled on the Eight Sleep app: pure black ground, near-black cards, a soft radial glow behind the
main temperature coloured by its value.

Two things the app will not do:

- **It will never show a value it has not confirmed.** The unit cannot be read over infrared, so
  most of what the app holds is belief rather than knowledge. Anything unconfirmed says `unknown`.
  At 3am a blank is better than a confident lie.
- **It is not an alarm.** The wake time drives the unit's temperature schedule and nothing else. A
  PWA cannot wake me reliably on iOS and the HydroSnooze has no vibration alarm anyway. My actual
  alarm stays in the Clock app, and the UI says so.

## The hardware

All of this is installed and running.

| Item | Roughly | What it does |
|---|---|---|
| Raspberry Pi | £35 to £70 | Runs the app, at `hydrosnooze.local` |
| XIAO Smart IR Mate | £11 | Sends the remote's infrared signals |
| Shelly Plug S Gen3 | £18 | Tells the app whether the unit is actually on |
| 3x DS18B20 probes | £10 | The mattress temperature, on one wire |
| Withings Sleep Analyzer | £120 | Next. The sleeper rather than the machine |

The frontend is built on my Mac and copied across as static files. The Pi never builds React, which
is why `frontend/` is a standalone Vite project with no Pi-side build step anywhere in it.
