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

## Parts

- [x] Shelly Plug S Gen3
- [x] XIAO Smart IR Mate
- [x] **Raspberry Pi 4, 4GB.** More than this needs, which means it will never be the thing that is
      too slow, and it is a comfortable machine to SSH into
- [ ] A decent A2 microSD card, 32GB (£10 to £20)
- [ ] The official Pi 4 power supply. Under-powering a Pi produces symptoms that look exactly like
      software bugs, which is why it is worth the money rather than a phone charger. The service
      reads the Pi's own throttling flags once an hour now and pushes a plain-words warning naming
      the supply, so this is at least a thing that gets found rather than guessed at
- [ ] A passive case. No fan: it does not need one and it may end up in a bedroom

Do not cheap out on the card. This runs day and night writing logs and power samples, and a cheap
card quietly failing is the most likely way the whole thing stops working. The app does its share:
the database uses WAL and batches power samples twenty at a time, which took a day of sampling from
11,544 fsyncs to 8, and `install.sh` caps the journal at 200M. A good card is the other half.

---

## An hour: the Shelly, on its own

Detail in [SETUP.md](SETUP.md#step-1-the-shelly). **Needs no Pi, no blaster and no Python setup.**
The plug joins the Wi-Fi and answers HTTP by itself, and the Mac is already on that network. This is
the whole reason it comes first.

### Get it on the network

- [x] Plug it into the wall on its own and give it thirty seconds
- [x] Set it up in the Shelly Smart Control app. **It needs the 2.4 GHz network**, which is where
      most Shelly setups stall
- [x] Write down its IP address: **192.168.1.194**
- [ ] Give it a fixed address in the router, if that is easy. Worth doing: the app is configured
      with the number, so a new one from the router means editing `.env` again
- [x] Open `http://<its IP>/rpc/Switch.GetStatus?id=0` and get JSON back with `apower` in it. That is
      the exact endpoint the service uses, so this one check proves the whole plug half
- [x] `curl -s http://192.168.1.194/rpc/WiFi.GetStatus` and read the `rssi`. Started at **-87 dBm**,
      which is weak enough to drop reads
- [x] Fix the bedroom coverage. **Done with a Wi-Fi extender: -50 dBm on `VM1876778_EXT`**, and read
      failures went from 5 in 59 to 0 in 44
- [ ] Put the Pi and the blaster on the extended network too when they go in. Same room, same
      problem, and the blaster is the one where a dropped connection costs a night rather than an
      `unknown`

### Set the auto-off timer while I am in the app

- [ ] Auto-off, 10 hours (36000 seconds on screens that want seconds). **Not confirmed done.** The
      one item on this page nothing in software can check for me

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

- [x] With the bed still warm, set warming to 25°C and watch the draw. **Answered: the assumption
      holds.** Flat 5 W for ninety seconds, against 166 W cooling and 306 W heating, so warming does
      not cool

Cooling covers 15 to 35°C and warming covers 25 to 55°C, so between 25 and 35 both modes hold the
same number and the app has to pick one. It picks from the direction the bed has to travel, and that
rested on warming only ever heating. The 5 W reading is itself the proof the bed was at or above
25°C, because below it the unit would have been heating. So the direction rule is doing real work.

### Run the app against the real plug

Half the hardware proven, a week early. `backend/hydrosnooze/adapters/shelly.py` has never run against anything
real, so any bug in it turns up now rather than on the evening I am also debugging a blaster.

- [x] Copy `backend/.env.example` to `backend/.env` and set `HS_POWER_MONITOR=shelly` and
      `HS_SHELLY_HOST=192.168.1.194`, leaving `HS_TRANSMITTER=fake`. **Both lines, including the
      address**: the mode alone leaves it talking to a default that is not there
- [x] `./scripts/dev.sh` and check the startup line reads `power=shelly at 192.168.1.194`
- [ ] Watch the app follow the unit. Power on with the physical remote and the Status card should
      change within thirty seconds, then set it cooling and watch it say `cooling`. That is the app
      observing real hardware through nothing but current draw, and it is the only closed loop in
      the project

The thresholds need no lines in `.env` any more. The measured values are the defaults.

---

## Evening one: the Raspberry Pi

Detail in [SETUP.md](SETUP.md#step-2-the-raspberry-pi).

- [ ] Download Raspberry Pi Imager onto the Mac
- [ ] Choose Raspberry Pi OS Lite (64-bit)
- [ ] **Click the settings gear before writing.** Hostname `hydrosnooze`, SSH on, username and
      password, Wi-Fi name and password, **locale and timezone**
- [ ] Put it on `VM1876778_EXT`, the extender network, same as the plug and the blaster
- [ ] Write the card, put it in, power on, wait two minutes
- [ ] `ssh liam@hydrosnooze.local` from the Mac gives a prompt
- [ ] `timedatectl` says `Europe/London` and `System clock synchronized: yes`

That gear step is what makes this painless. Skip it and the Pi never joins the network, and there is
no screen attached to tell me why.

**The timezone is not a detail.** The scheduler works in plain local time, so a Pi left on UTC runs
the whole night an hour early through British Summer Time, and nothing in the app can tell. A Pi also
has no battery-backed clock: it only knows the time because it asked the network. `install.sh`
checks both and warns, and the service prints the time it thinks it is in its first two log lines,
but the fix is one command:

```sh
sudo timedatectl set-timezone Europe/London
```

The Pi does not need to be near anything. It talks to the blaster and the plug over Wi-Fi, so it can
live wherever there is a spare socket and decent signal. Only the blaster needs to see the unit.

---

## Evening two: the blaster and the codes

Detail in [SETUP.md](SETUP.md#step-3-the-infrared-blaster). **This was the step everything hung on,
and it is done.** Captured 7 September on the Mac, no Pi involved.

- [x] Plug the XIAO into the Mac over USB
- [x] Install ESPHome on the Mac in its own venv, and fill in `docs/secrets.yaml`
- [x] Take the GPIO pin numbers from Seeed's published config rather than guessing. GPIO3 transmits,
      GPIO4 receives
- [x] Flash the capture configuration: `~/esphome/bin/esphome run docs/esphome-capture.yaml`
- [x] Run `./scripts/capture.py`. **Eight of eight clean on the first attempt**, three agreeing
      presses each, nothing inconsistent across the set

All eight came out as Symphony, 12 bits, 38kHz carrier:

| Button | | Code |
|---|---|---|
| `power` | | `0xDD2` |
| `schedule` | crescent moon | `0xD94` |
| `temp_up` | up arrow | `0xD82` |
| `temp_down` | down arrow | `0xDC3` |
| `cool` | snowflake | `0xD84` |
| `warm` | sun | `0xDB2` |
| `timer` | clock | `0xD81` |
| `mute` | speaker with X | `0xD88` |

Every one shares the top nibble `0xD`, which is the remote's address, and no two buttons produced the
same code. That is the cross-check the script does that I could not do by eye.

- [x] Fill them into `docs/esphome-hydrosnooze.yaml`
- [x] Flash it: `~/esphome/bin/esphome run docs/esphome-hydrosnooze.yaml`. The board is already on the
      Wi-Fi, so this can go over the air and the USB cable can stay out

### The one thing still unknown: does a press move one degree or several

The remote sends each code 8 to 12 times per tap and the unit still moves one step, so it is
ignoring repeats inside a burst. The config copies that. But I have never watched the unit while the
blaster sent anything, so this is belief, not measurement, and it is the last belief left.

**Test it before trusting anything else**, with the unit's own display in view:

- [ ] Open `http://hydrosnooze-ir.local` on the phone. Eight buttons, served by the board itself
- [x] Stand in front of the unit, tap `temp_up` once, and read the display
- [x] **It goes up by exactly one degree.** 29, 30, 31 on three taps. `ir_frames: "8"` is right
- [ ] It goes up by more than one, so the unit counts frames. Set `ir_frames: "1"` at the top of
      `docs/esphome-hydrosnooze.yaml`, reflash, try again, work up until one press is one degree
- [ ] Nothing happens at all. Aim and distance first, then raise `ir_frames`

Getting this wrong is the one thing that would break the rail-and-count sequence silently, because
every temperature the app sets would land a few degrees off and nothing would say so.

- [x] `power` turns the unit on and off
- [x] `cool` and `warm` change the mode

The web page is a setup and debugging tool only. The app talks to the board over the API, so nothing
in the running system depends on it.

---

## Two minutes: mute the unit

- [x] Fire the `mute` code once and check the beeping stops. **Done 8 September, it is quiet**

**Once only.** The unit remembers it through a power cut, so it is a toggle rather than a command.
Sending it again turns the beep back on, and the press that does it beeps. Nothing in the app does
this automatically; there is a button under the Status card for it.

Doing it by hand in daylight also matters because the wake preamble drops the unit's target by a
degree or two, and the app marks the temperature unknown afterwards rather than showing a number the
unit is not holding. Not something to trigger at 2am.

---

## Ten minutes: drive the real unit from the Mac

Detail in [SETUP.md](SETUP.md#step-6-drive-the-real-unit-from-the-mac). Worth doing before the Pi
exists. The Mac has the project, the blaster is on the Wi-Fi, and the plug has been answering for
days, so there is nothing left to wait for.

- [ ] `./scripts/use-hardware.py`, which points `backend/.env` at both devices and copies the ESPHome
      key across without it needing to be retyped
- [ ] `./scripts/dev.sh`. On real hardware it starts three things in one window and tags every
      line: `app` what it decided, `ir` that the infrared left the board, `plug` what the unit
      actually drew. One Ctrl-C stops all three
- [ ] The simulator tab has gone from the app. That is proof it is driving real infrared
- [ ] Power on with the button in the top right of the app, and the plug reading climbs off
      standby
- [ ] Set a temperature, and the unit's display lands on that number
- [ ] Set cooling, and the plug reads about 170 W. Set warming, and it reads about 300 W
- [ ] Power off

`./scripts/use-hardware.py --fake` goes back to the simulator whenever needed.

### Then rehearse a whole night before trusting it to run one

Four presses proves the blaster. It does not prove the night. Open the schedule behind the chevron on
the Wake card and press **Run a test night**: tonight's own stages, in order, at their own
temperatures and modes, compressed into about six minutes on the real unit.

- [ ] Get ready, Deep, REM, Wake, off. All five land, in order
- [ ] The unit's display shows the right number at each boundary
- [ ] The `plug` lines follow: about 170 W while cooling, about 300 W once Wake switches to warming
- [ ] It switches the unit off at the end without being told

That third one is the whole system proving itself: a belief checked against a measurement, and a
night that goes from cooling to heating, which the unit's own scheduler could never do.

That third-from-last one is the good test. It is the only place in the system where something the app
believes gets checked against something measured.

---

## Install and swap

Detail in [SETUP.md](SETUP.md#step-7-move-it-to-the-pi). One evening, in this order. The order is not
arbitrary: stopping the Mac before the Pi drives anything is what stops two schedulers pressing
buttons at the same unit, and stopping the service before copying the database is what stops it being
copied mid-write.

### Get the Pi up

- [ ] Raspberry Pi OS **Lite** 64-bit via Imager. **Gear icon before writing:** hostname
      `hydrosnooze`, SSH on, username and password, Wi-Fi on `VM1876778_EXT`, and **timezone**.
      **Re-flash even if the kit card came pre-loaded**: it has not been through that gear icon, so
      SSH is off and it will not join the Wi-Fi, and it is the desktop image rather than Lite
- [ ] Boot, wait two minutes, `ssh liam@hydrosnooze.local`
- [ ] `timedatectl` says `Europe/London` and `System clock synchronized: yes`. Both halves matter and
      they are different problems: the first is which timezone stage times are read in, the second is
      whether the Pi knows the time at all. It has no battery-backed clock, so at boot it believes it
      is whenever it last shut down until the network corrects it
- [ ] On the Pi: `git clone`, then `./scripts/install.sh`. **The clone is not what runs.** It is
      where the unit file and the scripts are read from; `install.sh` copies the backend to
      `/opt/hydrosnooze` and that is what systemd starts
- [ ] From the Mac: `./scripts/deploy.sh liam@hydrosnooze.local`

### Hand the hardware over

- [ ] **Ctrl-C the Mac's `dev.sh`.** Two schedulers driving one unit is the worst outcome of the night
- [ ] On the Mac: `./scripts/use-hardware.py --fake`, so running `dev.sh` later never drives the bed
- [ ] On the Mac: `./scripts/notify.py --heartbeat ""`. **This one is easy to skip and it matters.**
      The heartbeat is what raises the alarm when a machine goes quiet, and it cannot tell which
      machine is pinging. A Mac left pinging the same URL keeps the check green through a night the
      Pi spent switched off. `use-hardware.py --fake` does not cover this: it only touches the
      hardware lines
- [ ] `ssh liam@hydrosnooze.local 'sudo systemctl stop hydrosnooze'`
- [ ] From the Mac: `scp backend/data/hydrosnooze.db liam@hydrosnooze.local:/opt/hydrosnooze/data/`
      to carry the schedule and the measured lead times across
- [ ] From the Mac: `scp docs/secrets.yaml liam@hydrosnooze.local:~/Hydro-Snooze-app/docs/`
- [ ] On the Pi: `./scripts/use-hardware.py --env /opt/hydrosnooze/.env`
- [ ] `sudo systemctl restart hydrosnooze && journalctl -u hydrosnooze -f`

### Check it took

- [ ] The log says `transmitter=esphome at 192.168.1.178, power=shelly at 192.168.1.194`
- [ ] The next line says `Local time is ... (BST, UTC+01:00)`, not UTC
- [ ] `systemctl cat hydrosnooze | grep '^After='` shows **two** lines, `network-online.target` and
      `time-sync.target`. The service waits for the clock to be set before scheduling anything, and
      this is the systemd half of that. One line means the unit file did not get written
- [ ] `iw dev wlan0 get power_save` says `off`. Raspberry Pi OS leaves it on, and it causes exactly
      the dropped connections that cost the night on 9 September. `install.sh` turns it off
- [ ] `vcgencmd get_throttled` says `throttled=0x0`. Anything else and the power supply or the cable
      is the first thing to change, before debugging any software. The service watches this hourly
      and will say so on its own, but it is worth one look on the first evening
- [ ] `http://hydrosnooze.local:8000` on the phone, added to the Home Screen
- [ ] The device bar shows **four green chips**: Service, Blaster, Plug and Alerts. Alerts is the
      new one and it is amber until both the topic and the heartbeat are set, which is the next
      block. Amber there is correct at this point, not a fault
- [ ] The simulator tab has gone
- [ ] **Run a test night from the Pi.** This is the acceptance test for the whole move

### Same evening, because the Pi is load-bearing now

- [ ] **Carry the existing topic across rather than making a second one.** The phone is already
      subscribed to the one set up on 9 September, so read it off the Mac with
      `./scripts/notify.py`, then on the Pi:
      `./scripts/notify.py --env /opt/hydrosnooze/.env --topic <that string>`. Running it bare
      would generate a fresh topic and the phone would be listening to the old one
- [ ] `./scripts/notify.py --env /opt/hydrosnooze/.env --heartbeat <the healthchecks.io ping URL>`.
      Same URL as the Mac was using. The Pi taking over the pings is the point
- [ ] `sudo systemctl restart hydrosnooze`, then prove it: `./scripts/notify.py --test`. It asks
      the running service to send it, so arriving proves the Pi read the topic rather than just
      that ntfy works. The first real notification should not be the one at 2am
- [ ] The Alerts chip in the app has gone green
- [ ] Check the watchdog took, which only exists on the Pi. The service says so itself in the
      startup log: **`systemd is watching, pinging every 45s`**. If that line is missing, systemd
      is not watching and the unit file did not take. `systemctl show hydrosnooze -p NRestarts`
      is the number worth checking back on later: `0` means it has never needed saving
- [ ] `sudo apt install cockpit`, then `https://hydrosnooze.local:9090` for a dashboard
- [ ] Try `./scripts/diagnose.py` once while everything is working, so the command is familiar
      before the morning it is needed
- [ ] Give the Pi a fixed address in the router
- [ ] **Set the Shelly's 10 hour auto-off timer.** The last backstop no software of ours can fail to run

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

A restart mid-night is free now. Systemd brings the service back after a crash and the watchdog
brings it back after a stall, and it reads back which jobs already ran rather than reporting a night
that went fine as a night full of missed stages. Worth knowing while watching the log: a restart in
there is not a problem to chase.

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

## Changing anything after this

Detail in [SETUP.md](SETUP.md#updating-the-pi-later). Two copies live on the Pi and only one of them
runs, so it is worth reading once before the first upgrade rather than during it.

- Most changes: **`./scripts/deploy.sh liam@hydrosnooze.local`** from the Mac. Builds, copies,
  restarts. Never touches `.env` or the database
- Changes to the **systemd unit**, the **scripts**, or **dependencies**: on the Pi, `git pull` then
  `./scripts/install.sh`. `deploy.sh` does not rewrite the unit file, so a change there looks
  deployed and is not, which is the trap worth knowing about

---

## If something goes wrong

`journalctl -u hydrosnooze -n 100` on the Pi. The troubleshooting tables are at the bottom of
[SETUP.md](SETUP.md#when-something-goes-wrong) and
[GETTING-STARTED.md](GETTING-STARTED.md#when-something-goes-wrong).
