# Setting up the hardware

The work that turns the simulation into a bed that cools itself. Six steps, none of them a whole
evening except the blaster.

Written so that a version of me who has never used a Raspberry Pi can follow it. If a line looks
like nonsense, it is explained underneath.

**Read [GETTING-STARTED.md](GETTING-STARTED.md) first** if the app has not been run on the Mac yet.
Everything here assumes it has, because knowing what the press log looks like when it works is what
makes a broken one obvious.

**A word of honesty.** The code that talks to the blaster and the plug is written in full and has
never run against real equipment. That is no longer true of both halves at once: step 1 tests the
plug on its own, with nothing else in the project involved, so by the time I get to the blaster only
one untested thing is left. Expect to correct something in step 4 anyway.

---

## The order, and why

| Step | Needs | Roughly |
|---|---|---|
| 1. The Shelly | Nothing at all | An hour |
| 2. The Raspberry Pi | The Pi and a card | An evening |
| 3. The infrared blaster | The Pi | An hour |
| 4. Capture the eight codes | Step 3, and the physical remote | An evening |
| 5. Mute the unit | Step 4 | Two minutes |
| 6. Install and swap | Everything above | An hour |

The Shelly comes first because it is the only part that needs nothing else. It joins the Wi-Fi and
answers HTTP by itself, and the Mac is already on that network, so it can be measured and proven the
day it arrives.

### What to buy

| Item | Roughly |
|---|---|
| Raspberry Pi Zero 2 W, or a Pi 4 with 2GB | £35 to £70 |
| A decent A2 microSD card, or a small USB SSD | £10 to £20 |
| XIAO Smart IR Mate | £11 |
| Shelly Plug S Gen3 | £18 |

Do not cheap out on the card. This runs day and night writing logs, and a cheap card quietly failing
is the most likely way the whole thing stops working.

---

## Step 1: the Shelly

About an hour, and it needs no Pi, no blaster and no Python setup.

### Get it on the network

1. Plug it into the wall on its own, with nothing plugged into it. Wait about thirty seconds
2. Install the **Shelly Smart Control** app on the phone and let it find the plug over Bluetooth. It
   appears as something like `ShellyPlugSG3-XXXXXX`
3. Join it to the Wi-Fi. **It needs the 2.4 GHz network.** If the router broadcasts one name for
   both bands this usually just works; if 2.4 and 5 GHz have separate names, pick the 2.4 one. This
   is where most Shelly setups stall
4. Write down its IP address. It is in the app under the device's information, or in the router's
   list of connected devices
5. Give it a fixed address in the router if that is easy. Usually called a DHCP reservation. Skip it
   if the router makes it painful; it only matters so the address does not move later

If the app cannot find the plug at all, the fallback is its own Wi-Fi: connect the Mac to the
`ShellyPlugSG3-XXXXXX` network and open `http://192.168.33.1`.

### Check the signal before anything else

```sh
curl -s http://192.168.1.194/rpc/WiFi.GetStatus
```

That returns an `rssi` figure in dBm. Above -60 is fine, -60 to -70 is workable, and below -70 is
where reads start dropping.

**Mine came back at -87 the first time.** That is weak enough to matter, and it was worth stopping
on, because the plug is not the only thing that ends up in that room. The Pi has to stay reachable
all night, and the infrared blaster is a Wi-Fi device too. A dropped plug read only costs an
`unknown`. A dropped blaster means a stage boundary passes and the bed does not change.

So it is a coverage problem for the room, not a plug problem, and worth fixing before the blaster
arrives rather than after.

**Fixed with a Wi-Fi extender**, and the plug now sits on `VM1876778_EXT` at **-50 dBm**. That is 37
dB, which is a factor of about five thousand in signal power. It took the read failures from 5 in 59
to 0 in 44.

When the Pi and the blaster go in, put them on the extended network too. They are in the same room
with the same problem, and the blaster is the one where a dropped connection actually costs a night.

If the number is still poor after an extender, the order of things to try is:

1. **Move the plug out from behind the unit.** A short mains extension, so it is not sitting behind a
   metal chassis and a mattress. Free if there is one in a drawer, and often worth 10 dB.
2. **An access point or mesh node rather than a repeater.** If ethernet reaches the room, an access
   point beats anything wireless, and the Pi can go on a cable and stop caring entirely.
3. **Placement.** A repeater needs a *strong* link back to the router, so roughly halfway. Putting
   one next to the weak device just repeats a weak signal, which is the usual way these disappoint.

Re-run the command after any change. The number tells you immediately whether it helped.

### Prove it answers

Paste this into a browser on the Mac, with the real IP:

```
http://192.168.1.194/rpc/Switch.GetStatus?id=0
```

A blob of JSON comes back with `"apower"` in it. That is the exact endpoint the service uses, so
this one check proves the whole plug half of the project.

### Set the auto-off timer, while I am already in the app

Find the auto-off setting under the device's output options and set it to **10 hours**. Some screens
want seconds, in which case that is **36000**.

**This is not optional.** The app drives the night itself, so the unit never switches itself off,
and the software temperature ceiling is set to the unit's own maximum, so nothing in the app stops a
warming stage running at 55°C. If the Pi dies at 3am, this timer is the only thing left.

Doing it now rather than later means the backstop is in place before anything is ever left running
unattended.

### Read the four states

Plug the unit into the Shelly and the Shelly into the wall. Then on the Mac, in the project folder:

```sh
./scripts/plug.py 192.168.1.194
```

That reads the plug once a second and says which of the four states the service would call it, using
the service's own classifier so the two cannot drift apart. Put the unit into each state with the
physical remote, wait for the settled column to stop moving, and write it down.

| Do this on the remote | State | Mine, measured | Notes |
|---|---|---|---|
| Power the unit off | Off at the wall | **1.2 to 1.6 W** | standby only |
| On, set close to where the bed already is, left to stop working | Idle | **4.9 to 10.2 W** | 5 in warming, 9 in cooling |
| Cooling, set to 15°C | Cooling | **161 to 188 W** | settles around 166 |
| Warming, set to 40°C | Heating | **304 to 393 W** | settles around 310 |

Taken on 7 September off a King HS1001, 429 settled readings. Those four numbers are how the app
knows whether a power command actually worked. They are the only measured values in the whole
project; everything else it holds is belief.

**Idle has two levels, and that is the important part.** Warming idles at about 5 W and cooling at
about 9 W, so the gap between "off at the wall" and "on but doing nothing" is only three and a half
watts. The thresholds shipped before this measurement put the off line at 5 W, which read a unit
idling in warming as switched off. At a stage boundary the app would then have pressed power to turn
on a unit that was already on, which turns it off, in the middle of the night. Nothing but a real
reading would have found that.

### The one open assumption, now answered

Cooling covers 15 to 35°C and warming covers 25 to 55°C, so between 25 and 35 both modes can be set
to the same number and the app has to pick one. It picks from the direction the bed has to move: a
stage climbing into that band warms, a stage dropping into it, 30°C down to 25°C for instance,
cools. That rested on warming mode only ever heating.

**Tested, and it holds.** Bed already warm, warming set to 25°C, and the plug read a flat 5 W for
ninety seconds. Cooling draws 166 W and heating draws 306 W, so the unit was doing nothing at all.
The 5 W reading is itself the proof the bed was at or above 25°C, because below it the unit would
have been heating.

So a heater asked to make a bed colder sits there, exactly as assumed, and the direction rule is
doing real work rather than guarding against nothing.

### Run the app against the real plug

This is the half-step the plug makes possible, and it is worth taking. `backend/hydrosnooze/adapters/shelly.py`
has never run against hardware. Finding a bug in it now is much cheaper than finding it on the
evening I am also debugging infrared.

Copy the example settings and edit them:

```sh
cp backend/.env.example backend/.env
```

```
HS_TRANSMITTER=fake
HS_POWER_MONITOR=shelly
HS_SHELLY_HOST=192.168.1.194
```

Then the three thresholds. **These are boundaries between states, not the readings themselves**, so
each one goes roughly halfway between the two numbers it separates:

```
HS_OFF_THRESHOLD_W=3      # between 1.6 (standby) and 4.9 (idling in warming)
HS_IDLE_MAX_W=85          # between 10.2 (idling in cooling) and 161 (cooling)
HS_COOLING_MAX_W=245      # between 188 (cooling) and 304 (heating)
```

Then:

```sh
./scripts/dev.sh
```

Simulated unit, real plug. The status strip, the power chart and the History tab are all live
measurements now.

**Done when** the app shows the plug's real draw and the four states are written down.

---

## Step 2: the Raspberry Pi

One evening, and easier than it sounds.

1. Download **Raspberry Pi Imager** onto the Mac from raspberrypi.com/software
2. Open it. Choose the Pi model, then **Raspberry Pi OS Lite (64-bit)** under "Raspberry Pi OS
   (other)". Lite means no desktop, which is what is wanted
3. **Before writing, click the settings gear.** This is the step that makes the whole thing painless:
   - Hostname: `hydrosnooze`
   - Enable SSH, with password authentication
   - Username and password: pick something and write it down
   - Wi-Fi name and password, and set the country
4. Write it, put the card in the Pi, plug the Pi in, wait two minutes

That gear step means the Pi joins the network by itself on first boot, with no screen, no keyboard
and no mouse ever attached to it.

Then from Terminal on the Mac:

```sh
ssh liam@hydrosnooze.local
```

Whichever username was set. It asks about authenticity the first time: type `yes`.

**Done when** a prompt appears saying `liam@hydrosnooze`. Everything from here that says "on the Pi"
means typed into this window.

If `hydrosnooze.local` is not found, find the Pi's IP address in the router's admin page and use
that instead.

---

## Step 3: the infrared blaster

About an hour, **on the Mac**. No Pi needed for any of this.

The first flash has to go over USB whatever happens, so the machine with the USB port is the natural
host. Everything after that is over Wi-Fi. The Pi only becomes necessary at step 6, when something
has to stay running all night.

Plug the XIAO Smart IR Mate into the Mac with a USB-C cable, somewhere it can see the unit's
infrared receiver. It uses the ESP32-C3's native USB, so macOS needs no driver.

Install ESPHome on the Mac, in its own virtual environment so it does not tangle with anything else:

```sh
python3 -m venv ~/esphome
~/esphome/bin/pip install esphome
```

Then, from the project folder, put the Wi-Fi details somewhere ESPHome can find them:

```sh
cp docs/secrets.yaml.example docs/secrets.yaml
nano docs/secrets.yaml
```

Fill in the network name and password, and generate the API key it asks for with
`openssl rand -base64 32`. That file is gitignored and must stay that way.

**Put it on the same network the Shelly ended up on.** Same room, same weak spot, and the blaster is
the device where a dropped connection costs a night rather than an `unknown` on a screen.

### The pins, which are published rather than guessed

Seeed's own configuration for this exact board has them, so there is nothing to work out:

| | Pin |
|---|---|
| Infrared transmitter | GPIO3 |
| Infrared receiver | GPIO4 |
| Touch pad | GPIO5 |
| Vibration motor | GPIO6 |
| RGB LED | GPIO7 |

Source: [Seeed's xiao_smart_ir_mate.yaml](https://github.com/Seeed-Studio/xiao-esphome-projects/blob/main/projects/xiao_smart_ir_mate/xiao_smart_ir_mate.yaml).
They are already filled into both configurations in `docs/`.

The board arrives pre-flashed with Seeed's own configuration. Flashing over it is expected, and it
means the touch button and the vibration motor stop doing anything. Neither is used here.

### Build it before the board arrives

`esphome compile` does everything except talk to the board, so it can be done the day before. The
first run fetches the whole ESP-IDF toolchain and builds from scratch, which is where all the time
goes. Doing it in advance turns the first flash into a copy over USB.

```sh
~/esphome/bin/esphome compile docs/esphome-capture.yaml
```

Confirmed working, ESPHome 2026.8.2 against ESP-IDF 5.5.5:

```
hydrosnooze-ir.bin binary size 0xbf1e0 bytes
RAM:   [===       ]  32.2% (used 103412 bytes from 321296 bytes)
Flash: [====      ]  42.6% (used 782454 bytes from 1835008 bytes)
INFO Successfully compiled program.
```

Plenty of room, which also settles the `receive_symbols: 512` question: 512 symbols is about 2 kB
against 217 kB of DRAM still free.

The toolchain caches in `~/Library/Caches/esphome/`, and both configurations share the build
directory because they share a name, so the second one builds in a fraction of the time.

**Done when** ESPHome is installed, `docs/secrets.yaml` has real values in it, and the compile has
printed `Successfully compiled program.`

---

## Step 4: capture the eight codes

**This is the step everything else hangs on.** If the codes come out cleanly, the rest is
straightforward.

`docs/esphome-capture.yaml` is ready to flash as it is. It turns the board into a receiver that
prints every code it hears and does nothing else:

```sh
~/esphome/bin/esphome run docs/esphome-capture.yaml
```

Pick the USB port when it asks. The first build takes a few minutes; after that it stays connected
and streams the log.

To reconnect later, and keep a copy worth pasting back:

```sh
~/esphome/bin/esphome logs docs/esphome-capture.yaml | tee ~/Desktop/ir-capture.txt
```

**Press one button first and check something appears at all.** Nothing means the aim, the distance
or the flash is wrong, and there is no point capturing eight of nothing before finding that out.

If it cannot find the board, hold the small button on the IR Mate while plugging the cable in. That
forces the ESP32-C3 into its download mode.

### Let the script walk you through it

```sh
./scripts/capture.py
```

That starts the ESPHome log itself, asks for one button at a time, and waits for **three presses
that agree with each other** before moving on. It rejects and retries a button whose presses
disagree, and at the end writes a summary to the Desktop worth sending on.

It checks four things that are hard to see by eye:

- three presses of one button really produced the same code, allowing for the jitter that stops raw
  timings ever repeating exactly
- a button held a fraction too long is forgiven rather than counted as a different code
- all eight decoded as the same protocol and share one address
- no two buttons produced an identical code, which is what pressing the same button twice looks like

Press Enter to restart the button you are on, `q` to stop and keep what you have.

### Or by hand, which still works

Run the log directly, point the remote at the blaster from about 10cm, and press each of the eight
buttons once, slowly, watching what appears:

| Press this | Write it down as |
|---|---|
| Power | `power` |
| Crescent moon | `schedule` |
| Up arrow | `temp_up` |
| Down arrow | `temp_down` |
| Snowflake | `cool` |
| Sun | `warm` |
| Clock | `timer` |
| Speaker with X | `mute` |

**If they decode as a named protocol** such as NEC, with an address and a command, write those down.
That is far more reliable than raw timings and much shorter.

**If they only come out as raw timings**, that works too, it is just uglier.

The app never presses `schedule` or `timer`, because it stopped using the unit's own scheduler. They
are still worth capturing: they are two of the eight, and skipping them saves nothing.

### What came out

Captured 7 September, eight of eight clean on the first attempt. All eight decoded as **Symphony,
12 bits, 38kHz carrier**:

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

Every code shares the top nibble `0xD`, which is the remote's address, and no two buttons produced
the same code. Those are the two cross-checks that catch a bad capture which looks perfectly fine on
its own.

They are already written into `docs/esphome-hydrosnooze.yaml`, so there is nothing to fill in.

### Measured, so the transmitter can copy the remote rather than approximate it

Pulled out of the full capture log rather than eyeballed, over 24 presses:

| | Remote | ESPHome's Symphony encoder |
|---|---|---|
| Bit 1 mark | 1262 to 1315 µs | 1260 µs |
| Bit 0 mark | 421 to 447 µs | 460 µs |
| Bit time | constant 1710 µs | constant 1720 µs |
| Carrier | 38.0 kHz | 38.0 kHz |
| Frames per press | 8, 10 or 12 | set by config |
| Gap between frames | 7.2 to 9.2 ms | 34.76 ms |

The bit timings match closely enough that the receiver decoded every press first time. **The gap
between repeats is the one real difference**, which is why the config builds the burst out of single
frames spaced by hand rather than leaving it to ESPHome's own spacing. Two substitutions at the top
of the file, `ir_frames` and `ir_gap`, are the only numbers worth touching.

### Flash it

```sh
~/esphome/bin/esphome run docs/esphome-hydrosnooze.yaml
```

The board joined the Wi-Fi during the capture, so this can go over the air and the USB cable can
stay out. It validates clean against ESPHome 2026.8.2.

The names matter: the service looks for button entities called exactly `power`, `schedule`,
`temp_up`, `temp_down`, `cool`, `warm`, `timer`, `mute`.

### The first press is a measurement, not a demo

The remote sends each code 8 to 12 times per tap and the unit still moves one step, so the unit is
ignoring repeats inside a burst rather than counting them. The config copies what the remote does.
That is a well-founded assumption, but it is still an assumption, and it is the last one left in the
whole project.

So the first press has a job. Open **http://hydrosnooze-ir.local** on your phone, which is a page of
the eight buttons served by the board itself. **Stand where the unit's display is visible, tap
`temp_up` once, and read the number.**

Doing it from the phone rather than the Mac is the point. The number on the unit is the measurement,
so you have to be standing in front of it.

| What the display does | What it means | What to do |
|---|---|---|
| Up by exactly one | The unit ignores repeats, as expected | Nothing |
| Up by more than one | The unit counts frames | Set `ir_frames: "1"`, reflash, work back up |
| Nothing at all | Not received | Check aim and distance, then raise `ir_frames` |

**Measured 8 September: one press, one degree.** The display went 29, 30, 31 on three taps, so the
unit ignores repeats inside a burst and `ir_frames: "8"` is right. Nothing needed changing.

It also confirmed the quirk the whole design rests on, on real hardware for the first time. The
display was showing the **water** temperature, 23°C, and the first tap switched it to showing the
**target**, 29°C, without changing it. Only the taps after that moved the number. That is exactly
why every command starts with throwaway presses, and why `mute` marks the temperature unknown
afterwards rather than showing a figure the unit is not holding.

Worth the thirty seconds. If a press moves three degrees instead of one, the rail-and-count sequence
still runs, still reports success, and lands every temperature in the wrong place all night with
nothing to say so. That is exactly the class of bug this project is built to avoid.

**Done when** one press of `temp_up` moves the display by exactly one degree.

---

## Step 5: mute the unit, once

Two minutes, once presses can be fired on demand.

The app drives every part of the night itself, which means roughly thirty presses land at each stage
boundary, at two in the morning, next to a bed. The unit beeps on every one.

- Fire the `mute` code once and check the beeping stops

**Once only.** The unit saves this setting and keeps it through a power cut, so mute is a toggle
rather than a command. Sending it again turns the beep back on, and the press that does it beeps.
That is why nothing in the app sends it automatically, and why there is a button under the Status
card rather than a mute step in the nightly routine.

There is a second reason to do it by hand, in daylight. Every command starts with two throwaway
presses to wake the display, and on an already-awake display those two presses really do lower the
target by a degree. Every other command rails to a mode's floor and counts back up afterwards, which
absorbs them. Mute has nothing to count to, so the app marks the temperature unknown afterwards
rather than showing a number the unit is no longer holding. Fine as a setup action. Not something to
trigger at 2am.

If the `mute` code did not capture cleanly, go back to step 4 for that one button. It matters more
than it looks.

---

## Step 6: drive the real unit from the Mac

Ten minutes, and worth doing before the Pi exists. The Mac already has the project, the blaster is on
the Wi-Fi, and the plug has been answering for days. Everything the Pi will eventually do can be done
here first, in daylight, watching it.

### One command

```sh
./scripts/use-hardware.py
```

That edits `backend/.env`, which is the single file deciding whether the service drives hardware or a
simulation. It sets the transmitter to `esphome` and the power monitor to `shelly`, fills in both
addresses, and copies the ESPHome key straight out of `docs/secrets.yaml` so it never has to be
retyped. The key is never printed, only masked. Run it as often as you like; it rewrites its own
lines and leaves the rest of the file alone.

To go back to the simulator at any point:

```sh
./scripts/use-hardware.py --fake
```

### Then start it

```sh
./scripts/dev.sh
```

**How to tell it worked.** The simulator tab disappears from the app. It is shown only when
`HS_TRANSMITTER=fake`, so a missing spanner is proof the service is driving real infrared rather than
printing to a terminal.

### One window, three views

On real hardware `dev.sh` starts three things instead of one and tags every line with where it came
from, so there is still only one window to watch:

| Tag | What it is | How much to trust it |
|---|---|---|
| `app` | What the service decided, and what it believes the unit is on | Belief. It cannot read the unit back |
| `ir` | ESPHome's own log, proving the infrared left the board | Proof it was sent, not that it was received |
| `plug` | Watts the unit is actually drawing | The only measurement in the system |

A stage boundary should appear three times: about 33 press lines under `app`, the matching
`Sending Symphony` lines under `ir`, then half a minute later the wattage under `plug` climbing to
roughly 170 for cooling or 300 for heating.

The value is in the disagreements. `app` presses with no `ir` means the service cannot reach the
board. `ir` sending with no change in `plug` means the unit is not receiving them. Those are
different faults with different fixes, and one window separates them without any guessing.

One Ctrl-C stops all three. Nothing is left holding port 8000, which is the failure that makes the
next run look broken when it is only leftovers.

Against the simulator none of this happens: there is one process and the press log is the whole
story, exactly as before.

### Turning it on and off

The power button sits in the top right of every screen, so it is one tap from wherever you are. It
shows what the app knows rather than what it hopes:

| | |
|---|---|
| Green | On. Tapping turns it off |
| Grey | Off. Tapping turns it on |
| Amber, broken ring | The app does not know. Tapping turns it **off** |

Off is the right answer to not knowing. It is the safe direction, a unit already off ignores the
press, and powering off is verified against the plug, so one tap always ends somewhere known. The
ring is dashed as well as amber because green against amber is the pair a lot of people cannot
separate, and mistaking "no idea" for "on" is the confusion that matters most here.

Both actions read the plug before pressing anything and return early if the unit is already where
you asked, so neither can be sent twice by mistake.

### Daytime only, at first

Watch the unit and the terminal at the same time. Every press is still logged, exactly as it was
against the simulator, except now each line means a real burst of infrared.

- [ ] Power on with the button in the top right of the app, and check the plug reading climbs
      off standby
- [ ] Set a temperature, and check the display lands on that number
- [ ] Set a mode, and check the plug reading matches: about 170 W cooling, about 300 W heating
- [ ] Power off

That third one is the good test. It is the only place in the whole system where something the app
believes gets checked against something measured.

**Done when** setting a temperature from the phone puts that number on the unit's display.

### Then rehearse a whole night, in six minutes

Four presses proves the blaster works. It does not prove the **night** works, and that is a different
question: does every stage boundary fire, in order, on time, at the right temperature, in the right
mode, and does the unit really get switched off at the end. That is the thing I am about to trust
unattended, and it is the one thing neither the simulator nor the tests can answer, because the part
that has never run is the infrared arriving at a unit that is actually there.

So the app can run tonight's whole night compressed. Open the schedule behind the chevron on the
Wake card and press **Run a test night**.

It is not a demo and it is not a separate code path. It builds a night with short stages and hands it
to the same scheduler, so what runs is the same `due()`, the same fired marks, the same power checks
and the same rail-and-count sequences that will run at 2am. Only the durations differ.

Roughly six minutes:

| | |
|---|---|
| 0:00 | Get ready. Powers on, sets the pre-conditioning mode, rails to the first temperature |
| 0:45 | Deep |
| 3:00 | REM |
| 5:15 | Wake |
| 6:00 | Switches the unit off |

**Watch three things at once**, which is what the tagged terminal is for:

- the unit's display changing at each boundary, which is the thing being tested
- the `ir` lines confirming the presses left the board
- the wattage under `plug`, which should follow: around 170 W while a cooling stage runs, around
  300 W once the Wake stage switches to warming

That last one is the real proof. It is the only place in the system where a belief gets checked
against a measurement, and a night that goes from cooling to heating and back is exactly what the
unit's own scheduler made impossible.

It takes over from the real schedule while it runs and hands back afterwards, and it always finishes
by switching the unit off, including if it is stopped early. Do it when you are not about to go to
bed.

**Done when** all five steps land in order and the wattage follows the modes.

---

## Step 7: move it to the Pi

### Before the first boot

Raspberry Pi OS **Lite** 64-bit, written with Raspberry Pi Imager. Click the **settings gear before
writing**: hostname `hydrosnooze`, SSH on, username and password, Wi-Fi on `VM1876778_EXT`, and
**locale and timezone**.

**Re-flash the card even if the kit came with one pre-loaded.** A pre-loaded card has not been
through that gear icon, which means SSH is off, the hostname is `raspberrypi`, there are no Wi-Fi
credentials, and the timezone is whoever's it was. Without SSH or Wi-Fi none of that can be fixed
without plugging in a monitor and keyboard. Kits also ship the desktop image rather than Lite, which
on a machine running unattended for years means background writes to the one component most likely to
fail. Ten minutes with Imager removes all of it.

If you would rather keep what came on the card, it does work: boot it with a screen attached, then
`sudo raspi-config` for SSH, hostname, Wi-Fi, timezone, and Boot to Console. Then pick up below at
`git clone`.

The timezone is the one that matters and it is easy to skip. The scheduler works in plain local time,
so a Pi left on UTC runs the whole night an hour early through British Summer Time, and nothing in
the app can tell: it would look like the schedule is simply wrong. A Pi also has no battery-backed
clock, so it only knows the time because it asked the network on boot.

Two things guard this now. `install.sh` checks both and warns, and the service prints what it thinks
the time is in its first two log lines:

```
INFO  hydrosnooze  HydroSnooze up. transmitter=esphome at 192.168.1.178, power=shelly at 192.168.1.194
INFO  hydrosnooze  Local time is Tue 08 Sep 22:14 (BST, UTC+01:00). Stage times are read in this timezone.
```

If that says UTC in summer, fix it before anything else:

```sh
sudo timedatectl set-timezone Europe/London
```

**Where the Pi goes does not matter.** It reaches the blaster and the plug over Wi-Fi, so it needs no
line of sight to anything and does not have to be in the bedroom. Only the blaster needs to see the
unit.

### Then, on the Pi:

```sh
git clone https://github.com/liamdaly122/Hydro-Snooze-app.git
cd Hydro-Snooze-app
./scripts/install.sh
```

That sets up Python, installs the service, and registers it to start on boot. Then from the **Mac**,
in the project folder:

```sh
./scripts/deploy.sh liam@hydrosnooze.local
```

That builds the app on the Mac and copies it across. The Pi never builds the app; it has neither the
memory nor the patience.

### Stop the Mac first, before anything else

**This is the step that matters most and it is the easiest to forget.**

Until now the Mac has been driving the unit. The Pi is about to. If both are running, there are two
schedulers pressing buttons at the same unit: both fire every stage, both send their own thirty-odd
presses, and they interleave. The unit would end up on whatever the last press happened to say, and
the press logs would each look perfectly correct.

So on the **Mac**, before the Pi touches any hardware:

```sh
# In the terminal running dev.sh
Ctrl-C

# Then put the Mac back on the simulator, so running dev.sh again for
# development never drives the real bed by accident.
./scripts/use-hardware.py --fake
```

From here the Mac is a development machine again and the Pi owns the hardware.

### Take the history with it

A fresh install starts with an empty database. That throws away the schedule and, more to the point,
the pre-conditioning runs the plug has measured. Three nights of those are what let the app stop
estimating the lead time, so a week of them is worth carrying across.

Stop the service first, because copying a SQLite file out from under a running writer is how you get
a corrupt one:

```sh
ssh liam@hydrosnooze.local 'sudo systemctl stop hydrosnooze'
```

Then from the **Mac**, in the project folder:

```sh
scp backend/data/hydrosnooze.db liam@hydrosnooze.local:/opt/hydrosnooze/data/
```

Skip this if the Mac never ran against real hardware. Simulated nights are not worth carrying, and
the learned lead times from a fake unit would be actively wrong.

### Now the swap

`docs/secrets.yaml` is gitignored, so the clone on the Pi does not have it. Copy it across from the
**Mac** first, then let the same script do the edit it did here:

```sh
scp docs/secrets.yaml liam@hydrosnooze.local:~/Hydro-Snooze-app/docs/
```

Then on the **Pi**:

```sh
cd ~/Hydro-Snooze-app
./scripts/use-hardware.py --env /opt/hydrosnooze/.env
```

`--env` because the service runs from `/opt/hydrosnooze`, not from the clone. That writes the same
five lines it wrote on the Mac, with the key copied across rather than retyped:

```
HS_TRANSMITTER=esphome
HS_ESPHOME_HOST=192.168.1.178
HS_ESPHOME_ENCRYPTION_KEY=<copied from docs/secrets.yaml>
HS_POWER_MONITOR=shelly
HS_SHELLY_HOST=192.168.1.194
```

The three power thresholds are already the defaults, measured off this unit, so they only need
adding if the numbers ever change. Then:

```sh
sudo systemctl restart hydrosnooze
journalctl -u hydrosnooze -f
```

That second command shows what it is doing, live. Ctrl-C stops watching, it does not stop the
service.

**Two lines changed and it is driving real hardware.** That was the point of building it the way it
was built.

Then open `http://hydrosnooze.local:8000` in Safari on the phone and add it to the Home Screen.

The simulator tab is gone. It only ever appears when the transmitter is fake, so there is no way to
jump the clock on a unit that is genuinely running.

### Prove it, then trust it

Reading the log is not proof. **Run a test night from the Pi**, from the Test run card behind the
chevron on the Wake card. That is the acceptance test for the whole move: same five steps, same
order, wattage following the modes. If it passes, the Pi is doing exactly what the Mac was.

### Notifications, so a bad night does not wait until morning

The event log is thorough and useless while you are asleep. On 9 September the
system knew within seconds that the blaster had gone, and had no way to say so.

One command generates a topic and writes it into `.env`:

```sh
./scripts/notify.py
```

On the Pi, point it at the installed settings instead:

```sh
./scripts/notify.py --env /opt/hydrosnooze/.env
```

It prints a long random string. Install **ntfy** on the phone, tap +, and
subscribe to exactly that. There is no account and no key: the topic name is the
only secret there is, which is why it is generated rather than typed.

Restart the service, then **prove it rather than hoping**, because the first real
notification should not be the one at 2am:

```sh
./scripts/notify.py --test
```

`./scripts/notify.py --off` stops it. Running it again never invents a second
topic, and the hardware swap script leaves it alone.

Only problems are sent: anything at error level, plus the handful of warnings
that mean the night is not doing what it should. The thirty ordinary events of a
normal night are not, because a phone that buzzes at every stage boundary gets
muted, and then the one that mattered is muted too. The same problem is only sent
once every thirty minutes, so a stage retrying for its whole window is one push
rather than twenty.

### When something has gone wrong, one command collects the evidence

```sh
./scripts/diagnose.py
```

Writes a single file to the home directory with everything needed to work out
what happened: whether systemd has been restarting it and how often, the service
log, its own events, what it believes, what the plug measured, the settings, the
clock, and the state of the machine underneath.

**Secrets are masked before anything is written**, by name rather than by value,
so the infrared key and the notification topic never reach a file whose whole
purpose is to be pasted to someone else.

Worth running once while everything is fine, so the command is familiar before
the morning it is actually needed.

### A watchdog, for the failure Restart=always cannot catch

`Restart=always` catches a process that dies. It does not catch one that is
running perfectly and doing nothing useful, which is exactly what happened: the
scheduler spun for four hours getting nowhere and looked healthy throughout.

The unit file now sets `WatchdogSec=90`, and the service pings systemd **only
while the scheduler is completing ticks**. If ticks stop, the pings stop, and
systemd restarts it. Nothing to configure, and off a Pi it is all a no-op.

You can watch it working:

```sh
systemctl show hydrosnooze -p WatchdogTimestamp -p NRestarts
```

`NRestarts` climbing is the number worth knowing. Zero means it has never needed
saving.

### Three things to do the same evening

The Pi is load-bearing from tonight, so:

- **A dashboard for the Pi itself.** `sudo apt install cockpit`, then
  `https://hydrosnooze.local:9090`. CPU, disk, logs, the service list, and a terminal in the browser.
  Worth having before you need it rather than after
- **A fixed address for the Pi** in the router, alongside the plug and the blaster. A Pi that comes
  back on a new IP after a power cut still works over `hydrosnooze.local`, but a fixed one is one
  fewer thing to be surprised by
- **The Shelly's 10 hour auto-off timer.** This stops being optional the moment the Pi is the only
  thing switching the unit off. If it dies at 3am the bed stays exactly where it is, and that timer
  is the last backstop no software of ours can fail to run

---

## Earning trust before sleeping on it

The app works. That is not the same as trusting it overnight.

### Daytime tests only, at first

In the app: power on, power off, set a temperature, set a mode. **Watch the unit each time.**

In another Terminal window, watch what it is sending:

```sh
journalctl -u hydrosnooze -f
```

If the presses land, the codes are good and the hard part is behind me.

### Then watch a stage boundary land

Set a short stage in the app and stand in front of the unit when it changes. About thirty presses
over ten seconds: two throwaway presses to wake the display, a rail down to the mode's floor, then a
count up to the target.

There is no schedule to write any more. The app stopped arming the unit's own scheduler, so nothing
has to be walked through the setup wizard and nothing is unverifiable. This is the step that used to
be ninety presses and a warning that software could not check the result.

Worth doing twice, with boundaries that cross 25°C in each direction, so the mode switch gets watched
as well as the temperature. Going up should press `warm`; going down should press `warm` and then
`cool`.

### Then one night, with my normal alarm still set

Let it run a full night. In the morning, open the History tab and check:

- Did it power on before bedtime?
- Did each stage change at the right time?
- Did the app switch it off at the wake time? Nothing else will.

The event log says what it tried and the power chart says what actually happened. When those two
disagree, believe the chart: it is the only part of the screen that is measured.

### Then trust it

And keep the Shelly's auto-off timer set as a backstop.

---

## When something goes wrong

| Symptom | Where to look |
|---|---|
| App will not load at all | `sudo systemctl status hydrosnooze` on the Pi |
| App loads but is blank | The app was not copied across. Run `./scripts/deploy.sh` from the Mac |
| Everything says `unknown` | The Shelly is unreachable. Check `HS_SHELLY_HOST` matches its real IP, and that `./scripts/plug.py <ip>` still answers |
| Readings drop out now and then | `curl -s http://<plug ip>/rpc/WiFi.GetStatus` and read the `rssi`. Below -70 dBm is a coverage problem, not a software one. Every read is already retried once |
| Watts look right but the state is wrong | The three thresholds are not separating the four states. Re-read them with `./scripts/plug.py` and put each boundary halfway between |
| Presses sent, unit ignores them | The codes are wrong, or the blaster cannot see the unit. Back to step 4 |
| A stage did not change | Check the event log for a missed stage warning, then the power chart for whether the draw changed |
| A stage set the right number but the bed never moved | Check which mode it used. If a stage between 25 and 35°C is warming when the bed needed to come down, the assumption from step 1 was wrong |
| The unit was still on in the morning | Check the event log for the power off entry. Then check the Shelly's auto-off timer is set |
| Anything else | `journalctl -u hydrosnooze -n 100` |
