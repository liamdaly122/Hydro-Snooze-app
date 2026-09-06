# HydroSnooze

Local control for my HydroSnooze HS1001 bed cooler. An app on the iPhone Home Screen that sets the
temperature for each part of the night and the time I want to wake up, then does the rest by itself.

No subscription, no cloud, no account. It runs on a small computer in the house and talks to the
unit through an infrared blaster and a smart plug.

## Where this is up to

The hardware has not arrived yet. That does not block the app, so I am building
the app first.

**The whole thing works, end to end, against a simulated unit.** The service, the
scheduler, every command sequence, and the app driving all of it. What it is
missing is a real HS1001 on the other end, and swapping to one is two lines in
`.env`.

`ROADMAP.md` has the whole route from here to a working unit, in order, including
the parts only I can do.

## Running it

One command. It sets up Python, installs what it needs, builds the app, and
starts the service.

```sh
./scripts/dev.sh
```

Then open `http://localhost:8000`, or the address it prints for the phone, which
works from anything on the same Wi-Fi.

Every button press the service would have sent is printed in the terminal as it
happens. That press log is the most useful debugging tool in this project.

### Watching an evening without waiting for one

The app has a third tab, a spanner, which only appears when the service is
talking to a simulated unit. It has a clock that can be jumped.

Jump to 21:29, set the speed to 60x, and the whole evening plays out in about
half a minute: the unit powers on, forces Turbo, rails down and counts up to the
phase 1 temperature, waits until 22:00, arms the schedule, and drops out of Turbo
while the display is still awake from arming.

### Running the tests

```sh
cd backend && . .venv/bin/activate && pytest
```

96 of them. The ones that matter check that every temperature sequence lands on
exactly the right number from every plausible starting state, and that arming
without the wake preamble fails, which is the failure that would otherwise turn
up at 3am.

## The idea

The unit has no clock, no Wi-Fi and no way to be read back. Three facts make it controllable anyway:

1. **Its sleep schedule always runs 8 hours 30 minutes.** Three phases of 4h, 4h and 30m, fixed.
   So "wake me at 06:30" is exactly "arm the schedule at 22:00", and the unit never needs to know
   what time it is.
2. **It remembers its schedule through a power cut.** So the nightly routine is two button presses:
   power, then the sleep schedule button, then wait about 8 seconds while it arms itself with the
   temperatures it already has.
3. **Every temperature can be forced rather than tracked.** Press down 25 times to pin it at the
   mode's minimum, then count up to the target. Idempotent from any starting state, including after
   I have used the physical remote.

## Layout

```
backend/hydrosnooze/
  models.py            modes, ranges, rail counts, the night plan derivation
  clock.py             real, simulated and virtual time
  sequences.py         the button recipes: rail and count, the wake preamble
  scheduler.py         what should be happening, and when it is too late to bother
  service.py           everything wired together, and the only place holding state
  adapters/
    fake_unit.py       a simulated HS1001 with all the documented quirks
    fake_transmitter.py  prints every press instead of sending it
    esphome.py         the real infrared, untested against hardware
    shelly.py          the real plug, untested against hardware
frontend/src/
  types.ts             the same vocabulary, mirrored for the app
  api/client.ts        the interface between app and service
  api/http.ts          the live client
  api/mock.ts          seed data, for the Vercel copy
  screens/Dev.tsx      the time machine and the press log
scripts/dev.sh         run the whole thing on this machine
docs/esphome-*.yaml    a template to fill in with the captured codes
```

## Putting it on a phone while the design is being settled

Right now there is no service, only seed data, so the app is a plain static site and can be hosted
anywhere. There is a `vercel.json` at the root for exactly this: point Vercel at this repository and
it builds `frontend/` and gives me a URL I can open on the phone without my Mac being switched on.

Vercel scans the repository, sees `backend/pyproject.toml`, and decides this is a two-part app with
a website and a FastAPI service. It then refuses to deploy until it is told how to handle both. That
is the wrong shape here: there is no service in `backend/` yet, and when there is one it will not
live on Vercel.

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

## Hardware, once it arrives

| Item | Roughly | What it does |
|---|---|---|
| Small always-on computer | £35 to £70 | Runs the app |
| XIAO Smart IR Mate | £11 | Sends the remote's infrared signals |
| Shelly Plug S Gen3 | £18 | Tells the app whether the unit is actually on |

The frontend is built on my Mac and copied across as static files. The Pi never builds React, which
is why `frontend/` is a standalone Vite project with no Pi-side build step anywhere in it.
