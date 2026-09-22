# The bedside buttons

Three buttons on the bedside table: **warmer**, **cooler**, and **on/off**. At 3am
with my eyes shut, a phone is a terrible instrument. It has to be found, woken,
unlocked, and then the right screen has to load, and on the night of 16 September
it showed me a two hour hole in its own event log and let me conclude the whole
night routine had failed when it had worked perfectly. A button does one thing and
cannot be wrong about what it did.

They hang off the **same ESP32-C3 that already reads the temperature probes**. One
board, one cable run, one thing to power.

**The board never talks to the blaster.** It reports that a button went down and
nothing else, exactly as it reports a temperature. The Pi decides what that means
and sends the infrared. That is the same split the probes already use, and it is
what keeps the rule that all the thinking lives in one place.

---

## What to have in front of me

- **Three momentary push buttons**, normally open. Any single-pole button will do
- The **4-core signal cable**: one core for ground, three for the buttons
- The ESP32-C3 already running the probes, and its USB-C cable
- **No resistors.** The chip has internal pull-ups and they are enough here

The 4-core cable is exactly the right size for this, with nothing spare. Worth
knowing before I cut it to length.

---

## Step 1: pick the pins

GPIO4 is taken by the 1-Wire bus. The pins to stay away from are the same ones as
last time, and for the same reasons:

| Pin | Why not |
|---|---|
| **GPIO2, GPIO8, GPIO9** | strapping pins, read at boot to decide how to start |
| **GPIO20, GPIO21** | UART0, used by the serial logger |
| **GPIO4** | the probes are on it |

**Use GPIO5, GPIO6 and GPIO7.** They are free, they are next to each other on the
board, and three in a row is one less thing to get wrong when the cable is behind
a bed.

| Core | Pin | Button |
|---|---|---|
| 1 | **GND** | the common return for all three |
| 2 | **GPIO5** | warmer |
| 3 | **GPIO6** | cooler |
| 4 | **GPIO7** | on/off |

Write the colours down as I go. In six months the only record of which core is
which will be this table.

---

## Step 2: wire it

Every button gets one leg to its own GPIO and the other leg to the shared ground.
That is the whole circuit.

```
                                    ESP32-C3
   warmer                          ┌──────────┐
   ──o  o──────── core 2 ─────────►│ GPIO5    │
     │                             │          │
   cooler                          │          │
   ──o  o──────── core 3 ─────────►│ GPIO6    │
     │                             │          │
   on/off                          │          │
   ──o  o──────── core 4 ─────────►│ GPIO7    │
     │                             │          │
     └──────┬──── core 1 ─────────►│ GND      │
            │                      │          │
   probes ──┘   (spliced here)     │ GPIO4    │◄── probe data
                                   │ 3V3      │
                                   └──────────┘
```

**Splicing both grounds onto the one GND pin is correct.** Ground is a shared
reference, not a resource being divided up, and everything on this board draws
milliamps. One joint, both cables, no compromise.

The probes' 4.7kΩ resistor stays exactly where it is, pulling the 1-Wire data line
up to 3V3. The buttons have nothing to do with it and do not touch 3V3 at all.

### Why there are no resistors on the buttons

The chip can hold each input pin high by itself, through an internal pull-up of
around 45kΩ. So an unpressed button reads **high**, and pressing it connects the
pin to ground and drags it **low**.

That is backwards from how it reads, which is why the configuration below says
`inverted: true`. Pressed is low, and `inverted` turns low into "on".

**Buttons go to GND, never to 3V3.** With the pull-up enabled, a button wired to
3V3 does nothing at all, and it does nothing silently.

---

## Step 3: add them to the generator, not to the YAML

**This is the trap that will cost an evening if I get it wrong.**
`docs/esphome-probes.yaml` is not a file to edit. `scripts/probes.py` **writes it
from scratch** every time it runs, with `CONFIG.write_text(...)`. Hand-edit the
YAML, run `./scripts/probes.py --label ...` once more to relabel a probe, and the
buttons vanish with no warning.

So the buttons go into `scripts/probes.py`, beside the other blocks it assembles.
A new constant:

```python
BUTTONS = """
# The three bedside buttons. The board reports that one went down and nothing
# else: the Pi decides what a press means and sends the infrared, exactly as it
# does for a temperature.
#
# `inverted` because the internal pull-up holds the pin high and the button pulls
# it to ground, so electrically "pressed" is low.
#
# `delayed_on_off` is the debounce. A mechanical contact does not close once, it
# chatters for a few milliseconds, and without this one press arrives as five.
# This is the board's job and only the board's: it is an electrical problem
# measured in milliseconds. The Pi has a completely different settling problem
# measured in seconds, and it is dealt with separately.
binary_sensor:
  - platform: gpio
    pin:
      number: GPIO5
      mode:
        input: true
        pullup: true
      inverted: true
    name: "button_warmer"
    filters:
      - delayed_on_off: 20ms

  - platform: gpio
    pin:
      number: GPIO6
      mode:
        input: true
        pullup: true
      inverted: true
    name: "button_cooler"
    filters:
      - delayed_on_off: 20ms

  - platform: gpio
    pin:
      number: GPIO7
      mode:
        input: true
        pullup: true
      inverted: true
    name: "button_power"
    filters:
      - delayed_on_off: 20ms
"""
```

and then append it in `real_config`, after `RSSI` and before `RESTART`:

```python
        + RSSI
        + BUTTONS
        + RESTART
    )
```

**`binary_sensor` and not `button`.** ESPHome already uses a `button:` block in
this file for the restart control, and that is a software button that appears on
the web page for something to click. A physical input on a pin is a
`binary_sensor`. Putting these under `button:` would collide with the restart
entry and fail to compile, which is the good outcome. The bad outcome is
believing the two words mean the same thing.

---

## Step 4: flash it and prove the wiring before writing any code

```sh
./scripts/probes.py --flow 0x... --return 0x... --room 0x...
~/esphome/bin/esphome run docs/esphome-probes.yaml
```

Use the same three addresses already in the file, so nothing about the probes
changes. **Flash mode**, if it will not take: hold **BOOT**, tap **RST**, release
**BOOT**.

Then open `http://hydrosnooze-temp.local` and watch. The three buttons appear
alongside the temperatures, and each one flips from off to on and back as it is
pressed. The serial log says the same thing if the page is awkward to reach.

**Press each button ten times.** If one press ever registers as two, raise the
debounce from 20ms to 50ms. Cheap buttons are worse than good ones and there is no
prize for a small number here.

**Nothing on the Pi changes yet, and nothing breaks.** The adapter filters the
board's entities with `isinstance(entity, SensorInfo)`, so binary sensors are
invisible to it and always have been. The buttons can be wired, flashed and proven
before a line of service code is written.

### What a right one looks like, and what a wrong one looks like

I got this wrong first time, so it is worth writing down. `~/esphome/bin/esphome
logs docs/esphome-probes.yaml` streams the board without recompiling, which is the
thing to have open while pressing.

One press should produce exactly one name:

```
[16:25:29.491][S][binary_sensor]: 'button_cooler' >> ON
[16:25:29.666][S][binary_sensor]: 'button_cooler' >> OFF
```

What I actually had was every press firing two names at the same millisecond:

```
[15:36:43.348][S][binary_sensor]: 'button_power'  >> ON
[15:36:43.348][S][binary_sensor]: 'button_warmer' >> ON
```

and buttons that only responded at all when I held one down and pressed another.
That is one GPIO shorted straight to the ground core while the common return bus
floats. A single press then joins its pin to a floating node and nothing happens;
holding another button grounds that node through the shorted pin, and suddenly the
next press works. It reads like a software fault and it is a wire.

**Power the board off before rewiring.** Pull the USB. The log stream drops when
it does and reconnects on its own, which is expected and not a fault: uptime
restarting at a few seconds is how the log shows it rebooted.

Two things in that log that are not button faults but are worth reading anyway:

- **An OFF with no ON in front of it.** Harmless, and the Pi ignores it. The
  adapter only acts on a rising edge, so an odd release changes nothing
- **The signal strength line.** Mine read -79 to -92 dBm and ran a roam scan three
  times looking for something better. See the last trap in the table

---

## Step 5: teach the Pi to listen

**Built.** `backend/hydrosnooze/adapters/probes.py`, with the tests in
`backend/tests/test_buttons.py`.

The adapter already subscribed to **every** state the board sends, and `_on_state`
dropped anything whose key it did not recognise. So the change was small:
recognise the button entities as well, and keep them in their own dictionary.

```python
entities, _ = await client.list_entities_services()
self._keys = {
    entity.key: entity.name
    for entity in entities
    if isinstance(entity, SensorInfo) and entity.name in NAMES
}
self._buttons = {
    entity.key: entity.name
    for entity in entities
    if isinstance(entity, BinarySensorInfo) and entity.name in BUTTON_NAMES
}
```

**Keep them in a separate dictionary from the probes.** The next lines are:

```python
if not self._keys:
    raise RuntimeError(f"connected but found none of {', '.join(NAMES)}. ...")
```

That guard means "I connected to a board that is not the probe board". Let buttons
into `self._keys` and a board with three working buttons and three dead probes
would sail past it, which is precisely the fault that check exists to catch.

Then in `_on_state`, a key in `self._buttons` is a press rather than a reading, and
only the **rising edge** counts. The board sends a state for the release as well,
and letting that through would double every press.

Three things that only turned up once it was running against the real board:

- **The first state on a connection is not a finger.** aioesphomeapi replays what
  it holds for every entity when a subscription comes up, and on a link this weak
  that happens at 3am. A button whose state has not been seen since the link came
  up is recorded and nothing else
- **A repeated ON is not a second press.** The board sent one, and a held finger
  or a wobbly link will do it again
- **A press is not a reading.** It deliberately leaves `last_reading_at` alone.
  That clock decides whether the probes have gone quiet enough to tear the link
  down and rebuild it, and a press proving the Wi-Fi is fine would quietly stop
  the app complaining about a dead 1-wire bus

---

## Step 6: the settling rule, and why it is the Pi's job

**Built.** `BUTTON_SETTLE` in `backend/hydrosnooze/service.py`, 1.5 seconds.

There are two completely different settling problems here and they are solved in
two different places.

| | What it is | Where | How long |
|---|---|---|---|
| **Bounce** | a contact chattering as it closes | the board | 20ms |
| **Intent** | a person tapping a button several times | the Pi | about 1.5s |

The second one is the one that matters, and the reason is in the log from last
night:

```
21:48:14  Set mode to warming via warm then cool
21:48:29  Railed to 25C then counted up to 28C (35 + 3)
```

**A single temperature change is 38 presses of infrared and takes fifteen
seconds**, and it holds the command lock for all of them. Send one per button
press and three quick taps become three quarters of a minute of a unit being
hammered, with an app that looks dead for the duration and a bed that ends up
wherever the last one left it.

So the Pi counts rather than acts. Each press adds to a running total, and each
press restarts a 1.5 second timer. When the timer finally expires, **one** command
goes out for the net result. Three taps of warmer is one command for three
degrees, not three commands for one degree.

The on/off button is not part of that total, because a press is not a quantity. It
toggles, and any further presses inside the same window are ignored rather than
added up.

---

## Step 7: what each button should actually do

**Built.** `_button_pressed`, `_act_on_buttons`, `_button_temperature` and
`_button_power_toggle` in `backend/hydrosnooze/service.py`.

This is a decision rather than a detail, and it is worth making deliberately.

The obvious answer is `nudge_tonight`, which already exists and is what the app's
nudge row calls. **It is the wrong answer**, and the reason is in `models.py`:

```python
NUDGE_LIMIT_C = 1
NUDGE_MINUTES = 30
```

A nudge is clamped to **one degree** and expires after **thirty minutes**. Tapping
warmer five times at 3am would move the bed one degree and then quietly undo
itself before I woke up. That is right for a small tweak from the sofa and wrong
for the thing I reach for when the bed is actually uncomfortable.

**Use `set_stage_tonight` instead.** It changes the temperature of whichever stage
is running, for the rest of that stage, with no clamp and no expiry. It is exactly
what I reached for by hand at 22:20 last night when I set Drift to 29 and then
back to 27, and the buttons should do the thing I already do.

| Button | Does |
|---|---|
| **warmer** | current stage temperature, plus the net number of presses |
| **cooler** | current stage temperature, minus the net number of presses |
| **on/off** | one press, and the plug says which way it went. The same thing the remote's own button does |

Four cases the first version did not think about and now handles:

| | |
|---|---|
| **On/off and a temperature in the same window** | A fumble in the dark. It does the on/off and says the temperature was dropped, rather than setting a number on a unit whose state nothing knows |
| **The unit is off** | Nothing is sent, and the log says why. A button that does nothing and says nothing is a broken button |
| **On, but not inside a stage** | Run by hand in the evening, with no plan to edit. It moves whatever was last asked for, and refuses to guess if even that is unknown |
| **Already at the cap** | Says so. It stops at the cap rather than raising, because at 3am an exception in a log is the same thing as a dead button |

Every press writes a line to the event log under the kind `buttons`, whatever it
decides. That is the only way to tell afterwards whether a press reached the Pi,
reached the bed, or never left the bedside.

The safety cap still applies underneath all of this. `set_temperature` refuses
anything above `max_temperature_c` and anything outside the running mode's range,
and it refuses it before pressing a single button. A held-down finger cannot cook
the bed.

Holding a button to repeat is deliberately not in the first version. One press is
one degree, and that is enough to learn whether the idea is any good.

---

## Step 8: end to end

Everything above this line is done and covered by tests. This is the part that
needs the real bed, because it is the part no test can prove.

1. Press **warmer** three times, quickly
2. The app's event log should show **one** temperature change, not three
3. The unit's own display should land on the number the log claims
4. The bed should follow within a few minutes, on the probes

Number 3 is the one to actually look at. Infrared is one way and the app is
believing rather than knowing, so the display is the only independent check there
is that the presses arrived.

---

## The traps, in one place

| | |
|---|---|
| `docs/esphome-probes.yaml` is generated | Put the buttons in `scripts/probes.py` or lose them |
| `button:` is not a button | A physical input is a `binary_sensor:` |
| The release is a state too | Only the rising edge counts, or every press is doubled |
| Buttons to GND, never 3V3 | With the pull-up on, a 3V3 button does nothing, silently |
| One press is 38 presses of infrared | Coalesce over 1.5s, or three taps is 45 seconds of unit |
| `NUDGE_LIMIT_C` is 1 | Clamped to a degree and gone in half an hour. Not the right call for these |
| Buttons in `self._keys` | Breaks the guard that catches a board with dead probes |
| Two names per press | One GPIO shorted to ground, common return floating. Rewire, it is not software |
| A 4-leg tactile switch | Legs 1-2 and 3-4 are joined inside. Use two that are diagonally opposite, or the button is always closed |
| A reconnect is a state dump | The library replays every entity. The first state after a connect is recorded, never acted on |
| Signal at -85 dBm | The board can register a press perfectly and never deliver it. Shown on the probes health row now, so it explains a gap instead of being invisible |
| Nothing happens at the unit | Check the aim before the code. On 17 September the blaster was simply turned the wrong way and the infrared never left the bedside |
