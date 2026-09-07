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

About an hour.

Plug the XIAO Smart IR Mate into USB power, somewhere it can see the unit's infrared receiver.

Install ESPHome on the Pi:

```sh
sudo apt update
sudo apt install -y python3-venv
python3 -m venv ~/esphome
~/esphome/bin/pip install esphome
~/esphome/bin/esphome dashboard ~/esphome-configs
```

Open `http://hydrosnooze.local:6052` in a browser on the Mac. That is the ESPHome dashboard.

**Get the real GPIO pin numbers from Seeed's published configuration for this exact board on GitHub.
Do not guess them.** A wrong pin does nothing at all and gives no error, which is the worst possible
thing to debug.

`docs/esphome-hydrosnooze.yaml` in this repository is the configuration to fill in. It has the
capture section commented out at the bottom.

**Done when** the board appears in the dashboard and its logs stream.

---

## Step 4: capture the eight codes

**This is the step everything else hangs on.** If the codes come out cleanly, the rest is
straightforward.

Flash the capture configuration first, on its own: the `remote_receiver` block at the bottom of
`docs/esphome-hydrosnooze.yaml`, with everything else commented out.

Then open the ESPHome logs, point the remote at the blaster from about 10cm, and press each of the
eight buttons once, slowly, watching what appears:

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

Then fill them into the button section of the configuration and flash it properly. The names matter:
the service looks for button entities called exactly `power`, `schedule`, `temp_up`, `temp_down`,
`cool`, `warm`, `timer`, `mute`.

**Done when** pressing a button in the ESPHome dashboard makes the unit respond.

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

## Step 6: install it on the Pi

On the Pi:

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

### Now the swap

Edit the settings on the Pi:

```sh
nano /opt/hydrosnooze/.env
```

Change these two lines:

```
HS_TRANSMITTER=esphome
HS_POWER_MONITOR=shelly
```

And add the ESPHome details, plus the Shelly lines already worked out on the Mac in step 1:

```
HS_ESPHOME_HOST=hydrosnooze-ir.local
HS_ESPHOME_ENCRYPTION_KEY=the key from the ESPHome configuration
HS_SHELLY_HOST=192.168.1.194
HS_OFF_THRESHOLD_W=3
HS_IDLE_MAX_W=85
HS_COOLING_MAX_W=245
```

Save with Ctrl-O then Enter, exit with Ctrl-X. Then:

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
| Watts look right but the state is wrong | The three thresholds are not separating the four states. Re-read them with `./scripts/plug.py` and put each boundary halfway between |
| Presses sent, unit ignores them | The codes are wrong, or the blaster cannot see the unit. Back to step 4 |
| A stage did not change | Check the event log for a missed stage warning, then the power chart for whether the draw changed |
| A stage set the right number but the bed never moved | Check which mode it used. If a stage between 25 and 35°C is warming when the bed needed to come down, the assumption from step 1 was wrong |
| The unit was still on in the morning | Check the event log for the power off entry. Then check the Shelly's auto-off timer is set |
| Anything else | `journalctl -u hydrosnooze -n 100` |
