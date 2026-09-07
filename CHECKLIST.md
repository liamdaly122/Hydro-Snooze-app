# The checklist

Everything left, in order, with boxes to tick. [SETUP.md](SETUP.md) has the detail behind each step
and [ROADMAP.md](ROADMAP.md) has the reasoning.

The software half is finished, so nothing here is blocked on anything except parts arriving.

**The order matters now.** The Shelly needs nothing else in the project, so it goes first and can be
done the day it lands. The blaster needs the Pi to flash it. The install needs both. Everything after
that is patience.

---

## Already done

- [x] The app, designed and running on the phone
- [x] The service: scheduler, database, live updates
- [x] A simulated HS1001 with every documented quirk
- [x] Every button sequence, with 160 tests behind them
- [x] Verified on the Mac, press log and all
- [x] The app drives the night itself: Deep, REM and Wake, any durations, cooling and heating in the
      same night
- [x] A stage picks cooling or warming from the direction the bed has to move, not just the number

---

## Order the last part now

Do this before anything else, because it is the only thing with a delivery time attached.

- [ ] Raspberry Pi Zero 2 W, or a Pi 4 with 2GB (£35 to £70)
- [ ] A decent A2 microSD card, or a small USB SSD (£10 to £20)

Already ordered: the XIAO Smart IR Mate and the Shelly Plug S Gen3.

Do not cheap out on the card. This runs day and night writing logs, and a cheap card quietly failing
is the most likely way the whole thing stops working.

---

## An hour: the Shelly, on its own

Detail in [SETUP.md](SETUP.md#step-1-the-shelly). **Needs no Pi, no blaster and no Python setup.**
The plug joins the Wi-Fi and answers HTTP by itself, and the Mac is already on that network. This is
the whole reason it comes first.

### Get it on the network

- [ ] Plug it into the wall on its own and give it thirty seconds
- [ ] Set it up in the Shelly Smart Control app. **It needs the 2.4 GHz network**, which is where
      most Shelly setups stall
- [ ] Write down its IP address
- [ ] Give it a fixed address in the router, if that is easy
- [ ] Open `http://<its IP>/rpc/Switch.GetStatus?id=0` in a browser and get JSON back with `apower`
      in it. That is the exact endpoint the service uses, so this one check proves the whole plug
      half of the project
- [x] `curl -s http://192.168.1.194/rpc/WiFi.GetStatus` and read the `rssi`. Started at **-87 dBm**,
      which is weak enough to drop reads
- [x] Fix the bedroom coverage. **Done with a Wi-Fi extender: -50 dBm on `VM1876778_EXT`**, and read
      failures went from 5 in 59 to 0 in 44
- [ ] Put the Pi and the blaster on the extended network too when they go in. Same room, same
      problem, and the blaster is the one where a dropped connection costs a night rather than an
      `unknown`

### Set the auto-off timer while I am in the app

- [ ] Auto-off, 10 hours (36000 seconds on screens that want seconds)

**Not optional.** The app drives the night itself, so the unit never switches itself off, and the
temperature ceiling is the unit's own maximum of 55°C. If the Pi dies at 3am this timer is the only
thing standing between that and a bed that stays hot all day.

### Measure the four states

Plug the unit into the Shelly, then on the Mac:

```sh
./scripts/plug.py 192.168.1.194
```

Put the unit into each state with the physical remote, let it settle a minute or two, write down the
settled column:

- [x] Off at the wall: **1.2 to 1.6 W**
- [x] On and idling: **4.9 to 10.2 W** (5 in warming, 9 in cooling)
- [x] Cooling: **161 to 188 W**, settling around 166
- [x] Heating: **304 to 393 W**, settling around 310

Done on 7 September, 429 settled readings. These are the only measured numbers in the project;
everything else is a guess from the manual. They are now the defaults in `config.py`, so there is
nothing to copy into `.env` unless a different unit reads differently.

### Settle the one open assumption

- [ ] With the bed still warm, set warming to 25°C on the remote and watch the draw for two minutes

Cooling covers 15 to 35°C and warming covers 25 to 55°C, so between 25 and 35 both modes hold the
same number and the app has to pick one. It assumes warming only heats, so a stage dropping from
30°C to 25°C is run in cooling. Staying around 170 W means warming cools too and the assumption is
wrong. Falling to idle means it holds.

### Run the app against the real plug

Half the hardware proven, a week early. `backend/hydrosnooze/adapters/shelly.py` has never run against anything
real, so any bug in it turns up now rather than on the evening I am also debugging a blaster.

- [ ] Copy `backend/.env.example` to `backend/.env` and set `HS_POWER_MONITOR=shelly` and
      `HS_SHELLY_HOST=192.168.1.194`, leaving `HS_TRANSMITTER=fake`
- [ ] `./scripts/dev.sh`, and check the status strip, the power chart and the History tab are all
      showing real watts

The thresholds need no lines in `.env` any more. The measured values are the defaults.

---

## Evening one: the Raspberry Pi

Detail in [SETUP.md](SETUP.md#step-2-the-raspberry-pi).

- [ ] Download Raspberry Pi Imager onto the Mac
- [ ] Choose Raspberry Pi OS Lite (64-bit)
- [ ] **Click the settings gear before writing.** Hostname `hydrosnooze`, SSH on, username and
      password, Wi-Fi name and password, country set
- [ ] Write the card, put it in, power on, wait two minutes
- [ ] `ssh liam@hydrosnooze.local` from the Mac gives a prompt

That gear step is what makes this painless. Skip it and the Pi never joins the network, and there is
no screen attached to tell me why.

---

## Evening two: the blaster and the codes

Detail in [SETUP.md](SETUP.md#step-3-the-infrared-blaster). **This is the step everything hangs on.**

- [ ] Plug the XIAO into USB power where it can see the unit
- [ ] Install ESPHome on the Pi and open the dashboard at `hydrosnooze.local:6052`
- [ ] Get the real GPIO pin numbers from Seeed's published config on GitHub. **Do not guess.** A
      wrong pin does nothing at all and gives no error
- [ ] Flash the capture configuration from `docs/esphome-hydrosnooze.yaml`
- [ ] Point the remote at the blaster from 10cm and press each of the eight buttons, watching the log

Write down all eight:

- [ ] `power`
- [ ] `schedule` (crescent moon)
- [ ] `temp_up`
- [ ] `temp_down`
- [ ] `cool` (snowflake)
- [ ] `warm` (sun)
- [ ] `timer` (clock)
- [ ] `mute` (speaker with X)

- [ ] If they decode as a named protocol such as NEC, record the address and command. Far more
      reliable than raw timings
- [ ] Fill them into the button section and flash it properly
- [ ] Pressing a button in the ESPHome dashboard makes the unit respond

The app never presses `schedule` or `timer`, but capture them anyway. They are two of the eight and
skipping them saves nothing.

---

## Two minutes: mute the unit

- [ ] Fire the `mute` code once and check the beeping stops

**Once only.** The unit remembers it through a power cut, so it is a toggle rather than a command.
Sending it again turns the beep back on, and the press that does it beeps. Nothing in the app does
this automatically; there is a button under the Status card for it.

Doing it by hand in daylight also matters because the wake preamble drops the unit's target by a
degree or two, and the app marks the temperature unknown afterwards rather than showing a number the
unit is not holding. Not something to trigger at 2am.

---

## Install and swap

Detail in [SETUP.md](SETUP.md#step-6-install-it-on-the-pi).

- [ ] On the Pi: `git clone`, then `./scripts/install.sh`
- [ ] From the Mac, in the project folder: `./scripts/deploy.sh liam@hydrosnooze.local`
- [ ] Edit `/opt/hydrosnooze/.env` on the Pi and change the two lines:
      `HS_TRANSMITTER=esphome` and `HS_POWER_MONITOR=shelly`
- [ ] Add the ESPHome host and key, and copy across the Shelly IP and the three thresholds already
      worked out on the Mac
- [ ] `sudo systemctl restart hydrosnooze`
- [ ] Open `http://hydrosnooze.local:8000` on the phone and add it to the Home Screen
- [ ] The spanner tab is gone, which is how I know it is on real hardware

---

## Earn trust before sleeping on it

Detail in [SETUP.md](SETUP.md#earning-trust-before-sleeping-on-it).

### Daytime only, at first

Watch the unit each time, and watch `journalctl -u hydrosnooze -f` in a Terminal.

- [ ] Power on
- [ ] Power off
- [ ] Set a temperature
- [ ] Set a mode

If the presses land, the codes are good and the hard part is behind me.

### Then watch a stage change

- [ ] Watch one stage boundary land, and check the unit takes the new temperature

About thirty presses over ten seconds. There is no schedule to write any more, so nothing has to be
walked through the unit's setup wizard and nothing is unverifiable.

- [ ] Watch one boundary that crosses 25°C in each direction, and check the mode switches with it

### Then one night, with my normal alarm still set

- [ ] Let it run a full night
- [ ] In the morning, check History: did it power on before bedtime, change at each stage boundary,
      and get switched off at the wake time?
- [ ] Check the power chart agrees with the event log. The log says what it tried, the chart says
      what actually happened

### Then trust it

- [ ] Turn off the phone alarm safety net, if I want to
- [ ] Confirm the Shelly auto-off timer is still set

---

## If something goes wrong

`journalctl -u hydrosnooze -n 100` on the Pi. The troubleshooting tables are at the bottom of
[SETUP.md](SETUP.md#when-something-goes-wrong) and
[GETTING-STARTED.md](GETTING-STARTED.md#when-something-goes-wrong).
