# HydroSnooze

Local control for my HydroSnooze HS1001 bed cooler. An app on the iPhone Home Screen that sets the
temperature for each part of the night and the time I want to wake up, then does the rest by itself.

No subscription, no cloud, no account. It runs on a small computer in the house and talks to the
unit through an infrared blaster and a smart plug.

## Where this is up to

The hardware has not arrived yet. That does not block the app, so I am building the app first.

**Right now this repository contains the design**, running on seed data, plus the shared domain
layer both halves depend on. The service that drives the real unit comes next.

The important thing about the design push is that it is not a throwaway prototype. Every screen
talks to the `ApiClient` interface in `frontend/src/api/client.ts`, and the seed data is one
implementation of it. When the service exists, `HttpApiClient` implements the same interface and
`main.tsx` changes by one line. Nothing gets built twice.

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
backend/hydrosnooze/models.py    the domain: modes, ranges, rail counts, the night plan derivation
backend/tests/                   the derivations that everything else is built on
frontend/src/types.ts            the same vocabulary, mirrored for the app
frontend/src/domain.ts           night plan, temperature tinting, formatting
frontend/src/api/client.ts       the interface between app and service
frontend/src/api/mock.ts         seed data, the one temporary file in here
frontend/src/components/         cards, tabs, controls
scripts/make_icons.py            regenerates the PWA icons
```

## Running the app

```sh
cd frontend
npm install
npm run dev
```

The dev server binds to all interfaces, so it is reachable from my phone on the home Wi-Fi at
`http://<mac-hostname>.local:5173`. That is the only honest way to judge a design meant to be used
one-handed in a dark bedroom.

To check a production build:

```sh
npm run build && npm run preview
```

## Putting it on a phone while the design is being settled

Right now there is no service, only seed data, so the app is a plain static site and can be hosted
anywhere. There is a `vercel.json` at the root for exactly this: point Vercel at this repository and
it builds `frontend/` and gives me a URL I can open on the phone without my Mac being switched on.

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
