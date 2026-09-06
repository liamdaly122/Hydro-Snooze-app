# Setting up the hardware

The six evenings-worth of work that turns the simulation into a bed that cools itself.

Written for someone who has never used a Raspberry Pi. If a line looks like nonsense, it is
explained underneath.

**Read `GETTING-STARTED.md` first** if the app has not been run on the Mac yet. Everything here
assumes it has, because knowing what the press log looks like when it works is what makes a broken
one obvious.

**A word of honesty.** None of the hardware steps below have been tested by me against real
equipment, because none of it existed when this was written. The parts that talk to the blaster and
the plug are written in full and never run. Expect to correct something in step 3.

---

## Before starting

| Item | Roughly |
|---|---|
| Raspberry Pi Zero 2 W, or a Pi 4 with 2GB | £35 to £70 |
| A decent A2 microSD card, or a small USB SSD | £10 to £20 |
| XIAO Smart IR Mate | £11 |
| Shelly Plug S Gen3 | £18 |

Do not cheap out on the card. This runs day and night writing logs, and a cheap card quietly failing
is the most likely way the whole thing stops working.

---

## Step 1: the Raspberry Pi

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

Use whichever username was set. It will ask about authenticity the first time: type `yes`.

**Done when** a prompt appears that says `liam@hydrosnooze`. Everything from now on that says "on the
Pi" means typed into this window.

If `hydrosnooze.local` is not found, find the Pi's IP address in the router's admin page and use that
instead.

---

## Step 2: the infrared blaster

One evening.

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

---

## Step 3: capture the eight codes

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

Then fill them into the button section of the configuration and flash it properly. The names matter:
the service looks for button entities called exactly `power`, `schedule`, `temp_up`, `temp_down`,
`cool`, `warm`, `timer`, `mute`.

**Done when** pressing a button in the ESPHome dashboard makes the unit respond.

---

## Step 4: answer the one open question

Ten minutes, now that presses can be fired on demand.

The unit's schedule setup wizard arms itself with saved temperatures after about eight seconds of no
input. What is not known is whether that works from **any** phase, or only from phase 1.

Press the schedule button to enter setup, then press it once more to reach phase 2. Then wait twenty
seconds without touching anything.

- **It armed itself:** good, the default is right, nothing to change
- **It did not:** set `HS_SIM_AUTO_APPLY_FROM_ANY_PHASE=false` in `.env`

This blocks nothing. It only changes how much the app should trust a single attempt at arming, and
whether arming is genuinely self-correcting when a press gets dropped.

---

## Step 5: the Shelly

About half an hour.

Plug it in between the wall socket and the unit. Follow its instructions to join the Wi-Fi, and
**write down the IP address** it ends up with. Give it a fixed address in the router if that is easy,
so it does not move later.

Then read the watts in four states and write each one down:

| State | Expected, roughly | Yours |
|---|---|---|
| Unit off at the wall | under 5 W | |
| On, sitting at temperature | 5 to 60 W | |
| Actively cooling | around 170 W | |
| Actively heating | around 300 W | |

Those four numbers are how the app knows whether a power command actually worked. Until they are
measured they are guesses from the manual.

**While here:** set the Shelly's own auto-off timer, in its own app, to something like ten hours.
That is an independent backstop that switches the unit off even if the Pi is dead.

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

And add what was collected in steps 2 to 5:

```
HS_ESPHOME_HOST=hydrosnooze-ir.local
HS_ESPHOME_ENCRYPTION_KEY=the key from the ESPHome configuration
HS_SHELLY_HOST=192.168.1.50
HS_OFF_THRESHOLD_W=5
HS_IDLE_MAX_W=60
HS_COOLING_MAX_W=220
```

Save with Ctrl-O then Enter, exit with Ctrl-X. Then:

```sh
sudo systemctl restart hydrosnooze
journalctl -u hydrosnooze -f
```

That second command shows what it is doing, live. Ctrl-C stops watching, it does not stop the
service.

**Two lines changed and it is now driving real hardware.** That was the point of building it the way
it was built.

Then open `http://hydrosnooze.local:8000` in Safari on the phone and add it to the Home Screen.

Note the simulator tab is gone. It only ever appears when the transmitter is fake, so there is no way
to jump the clock on a unit that is genuinely running.

---

## Earning trust before sleeping on it

The app works. That is not the same as trusting it overnight.

### Daytime tests only, at first

In the app: power on, power off, set a temperature, set a mode. **Watch the unit each time.**

In another Terminal window, watch what it is sending:

```sh
journalctl -u hydrosnooze -f
```

If the presses land, the codes are good and the hard part is behind you.

### Then write the schedule, standing in front of it

This is the one that sends about ninety presses in a row and walks the unit through its setup wizard.
The app warns before starting, because **nothing in software can check what actually got written.**

Watch the unit step through three phases. If it does not, the codes or the timing need another look.

### Then one night, with the normal alarm still set

Let it run a full night. In the morning, open the History tab and check:

- Did it power on around 21:30?
- Did it arm at 22:00?
- Did it switch itself off at 06:30?

The event log says what it tried and the power chart says what actually happened.

### Then trust it

And keep the Shelly's auto-off timer set as a backstop.

---

## When something goes wrong

| Symptom | Where to look |
|---|---|
| App will not load at all | `sudo systemctl status hydrosnooze` on the Pi |
| App loads but is blank | The app was not copied across. Run `./scripts/deploy.sh` from the Mac |
| Everything says `unknown` | The Shelly is unreachable. Check `HS_SHELLY_HOST` matches its real IP |
| Presses sent, unit ignores them | The codes are wrong, or the blaster cannot see the unit. Back to step 3 |
| It armed but nothing happened overnight | Check the event log for the arming entry, then the power chart for whether the draw changed |
| Anything else | `journalctl -u hydrosnooze -n 100`, and send me the output |
