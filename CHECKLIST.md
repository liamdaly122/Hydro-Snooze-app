# The checklist

Everything left, in order. Tick as you go. Each step links to the detail.

Nothing here is blocked on anything except the parts arriving, and the software half is finished, so
you can do these whenever suits.

---

## Already done

- [x] The app, designed and running on the phone
- [x] The service: scheduler, database, live updates
- [x] A simulated HS1001 with every documented quirk
- [x] Every button sequence, with 117 tests behind them
- [x] Verified on your Mac, press log and all
- [x] The app drives the night itself: Deep, REM and Wake, any durations, cooling and heating in
      the same night

---

## Buy the last part

- [ ] Raspberry Pi Zero 2 W, or a Pi 4 with 2GB (£35 to £70)
- [ ] A decent A2 microSD card, or a small USB SSD (£10 to £20)

Already ordered: the XIAO Smart IR Mate and the Shelly Plug S Gen3.

Do not cheap out on the card. This runs day and night writing logs, and a cheap card quietly failing
is the most likely way the whole thing stops working.

---

## Evening one: the Raspberry Pi

Detail in [SETUP.md](SETUP.md#step-1-the-raspberry-pi).

- [ ] Download Raspberry Pi Imager onto the Mac
- [ ] Choose Raspberry Pi OS Lite (64-bit)
- [ ] **Click the settings gear before writing.** Hostname `hydrosnooze`, SSH on, username and
      password, Wi-Fi name and password, country set
- [ ] Write the card, put it in, power on, wait two minutes
- [ ] `ssh liam@hydrosnooze.local` from the Mac gives a prompt

That gear step is what makes this painless. Skip it and the Pi never joins the network.

---

## Evening two: the blaster and the codes

Detail in [SETUP.md](SETUP.md#step-2-the-infrared-blaster). **This is the step everything hangs on.**

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

---

## Two minutes: mute the unit

- [ ] Fire the `mute` code once and check the beeping stops

**Once only.** The unit remembers it. Sending it again turns the beep back on. Nothing in the app
does this automatically; there is a button under the Status card for it.

---

## Half an hour: the Shelly

Detail in [SETUP.md](SETUP.md#step-5-the-shelly).

- [ ] Plug it in between the wall and the unit, join it to the Wi-Fi
- [ ] Write down its IP address, and give it a fixed one in the router if that is easy

Measure and write down the watts in four states:

- [ ] Unit off at the wall (expect under 5 W)
- [ ] On, sitting at temperature (expect 5 to 60 W)
- [ ] Actively cooling (expect around 170 W)
- [ ] Actively heating (expect around 300 W)
- [ ] Set the Shelly's own auto-off timer to about ten hours. **Not optional.** The unit no longer
      switches itself off, so if the Pi dies mid-night this is the only thing that stops the bed
      running all day

---

## Install and swap

Detail in [SETUP.md](SETUP.md#step-6-install-it-on-the-pi).

- [ ] On the Pi: `git clone`, then `./scripts/install.sh`
- [ ] From the Mac, in the project folder: `./scripts/deploy.sh liam@hydrosnooze.local`
- [ ] Edit `/opt/hydrosnooze/.env` on the Pi and change the two lines:
      `HS_TRANSMITTER=esphome` and `HS_POWER_MONITOR=shelly`
- [ ] Add the ESPHome host and key, the Shelly IP, and the four power readings
- [ ] `sudo systemctl restart hydrosnooze`
- [ ] Open `http://hydrosnooze.local:8000` on the phone and add it to the Home Screen
- [ ] The spanner tab is gone, which is how you know it is on real hardware

---

## Earn trust before sleeping on it

Detail in [SETUP.md](SETUP.md#earning-trust-before-sleeping-on-it).

### Daytime only, at first

Watch the unit each time, and watch `journalctl -u hydrosnooze -f` in a Terminal.

- [ ] Power on
- [ ] Power off
- [ ] Set a temperature
- [ ] Set a mode

If the presses land, the codes are good and the hard part is behind you.

### Then watch a stage change

- [ ] Watch one stage boundary land, and check the unit takes the new temperature

About thirty presses over ten seconds. There is no schedule to write any more, so nothing has to be
walked through the unit's setup wizard and nothing is unverifiable.

### Then one night, with your normal alarm still set

- [ ] Let it run a full night
- [ ] In the morning, check History: did it power on before bedtime, change at each stage boundary,
      and get switched off at the wake time?

### Then trust it

- [ ] Turn off the phone alarm safety net, if you want to
- [ ] Confirm the Shelly auto-off timer is still set

---

## If something goes wrong

`journalctl -u hydrosnooze -n 100` on the Pi, and send me the output. The troubleshooting tables are
at the bottom of [SETUP.md](SETUP.md#when-something-goes-wrong) and
[GETTING-STARTED.md](GETTING-STARTED.md#when-something-goes-wrong).
