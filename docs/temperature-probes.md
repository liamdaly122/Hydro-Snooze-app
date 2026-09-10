# The temperature probes

Everything the system knows about the bed today is one number: how many watts the
unit draws. `DeviceState` says so out loud, with `observed_power_w` sitting alone
among a family of fields named `assumed_`.

Three probes on one wire change that. They are the second real measurement in the
project, and the first one that is about the bed rather than about the machine.

**No YAML is edited by hand anywhere below.** `scripts/probes.py` writes the
configuration at each of the three stages and asks ESPHome whether it is valid
before saying it is done, because a mistyped sixteen-character address fails long
after it is typed and names neither the file nor the cause.

---

## What to have in front of me

- The **ESP32-C3 SuperMini** and a USB-C cable that carries data, not just power
- **Three DS18B20 probes**, the waterproof sort with three flying leads
- **One 4.7kΩ resistor**. Just one, for the whole bus, whatever the probe count
- A breadboard or screw terminals, unless I am soldering
- A USB power supply for where it will finally live

If the probes came without a resistor, anything from 3.3kΩ to 4.7kΩ does the job.
Long cables prefer the lower end.

---

## Step 1: pick the pin, and avoid three of them

The ESP32-C3 reads three pins at boot to decide how to start. Pull one the wrong
way and the board does not come up, which is a confusing thing to debug with a
probe in hand. All three are broken out on this board:

| Pin | Why not |
|---|---|
| **GPIO2** | strapping pin |
| **GPIO8** | strapping pin, and the onboard blue LED is on it |
| **GPIO9** | strapping pin, and the BOOT button pulls it low |
| **GPIO20, GPIO21** | UART0, used by the serial logger |

**Use GPIO4.** GPIO3, 5, 6, 7 and 10 are equally fine if something else gets in
the way.

## Step 2: wire it

All three probes share the same three wires. That is what 1-Wire means, and it is
why three probes need no more connections than one.

```
  probe red    ──────────────────►  3V3
  probe black  ──────────────────►  GND
  probe yellow ────────┬─────────►  GPIO4
                       │
                     4.7kΩ
                       │
                      3V3
```

Join all three reds together, all three blacks together, all three yellows
together. **One resistor** between the joined yellows and 3V3.

Do this on a desk, not on the floor by the bed. Everything up to step 6 happens
where there is light and a laptop.

## Step 3: find out what the probes are called

Each DS18B20 has a unique 64-bit serial burned in at the factory, and all three
are needed before the real configuration can be written.

First a **new** API key. Not the blaster's: one leaked string should not open two
devices.

```sh
./scripts/probe-key.py
```

That generates it, writes it into `docs/secrets.yaml` under the name the config
below expects, and leaves everything already in that file alone. Running it twice
never makes a second key.

It does not print the key, because there is nothing to do with it by hand. The
config refers to it as `!secret hydrosnooze_temp_api_key` and flashing picks it up
with no further steps. `--show` prints it in full if it is ever needed for
`backend/.env`.

The point of the script is not saving typing. It is that pasting 44 random
characters into a hidden file by hand fails silently, and fails later, as an
unhelpful "invalid encryption key" at flash time.

Then write the discovery configuration and flash it:

```sh
./scripts/probes.py
~/esphome/bin/esphome run docs/esphome-probes.yaml
```

The first command writes `docs/esphome-probes.yaml` with the 1-Wire bus on GPIO4
and **no sensors at all**, then asks ESPHome whether it is valid before saying it
is done. Its only job is to look at the wire and report what is there.

**Getting it into flash mode**, if it will not take: hold **BOOT**, tap **RST**,
release **BOOT**. That is this board's quirk and it will be needed at least once.

The log prints what it found:

```
[one_wire] Found devices:
[one_wire]   0x1c0000031edd2828
[one_wire]   0x3a00000320f18b28
[one_wire]   0x9b000003215c4f28
```

**Three addresses means the wiring is right.** Fewer means a bad joint or a
missing resistor. None at all means the data wire is not on GPIO4.

## Step 4: work out which is which

The addresses come in no useful order, so identify them physically. Feed the three
straight back in, pasted however they come:

```sh
./scripts/probes.py --label 0x1c0000031edd2828 0x3a00000320f18b28 0x9b000003215c4f28
~/esphome/bin/esphome run docs/esphome-probes.yaml
```

That names them `probe_1`, `probe_2` and `probe_3` and reads every **10 seconds**
rather than 30, so a warming probe shows up while the hand is still on it.

The whole log block can be pasted instead, timestamps and all, if that is easier:

```sh
./scripts/probes.py --label < what-i-copied.txt
```

Now, watching the log:

1. **Squeeze one probe in a fist.** Within a few seconds one reading climbs. That
   is the one being held
2. **Put tape on that lead** and write `probe_1`, or whichever it turned out to be
3. Let it cool, then do the next

**Do not skip the tape.** Ten minutes later all three look identical and it is
guesswork again.

## Step 5: the real configuration

One command, with each address against the job it is doing:

```sh
./scripts/probes.py \
  --flow 0xba0000002618c028 \
  --return 0x5c00000000e73f28 \
  --room 0x2900000025d73f28

~/esphome/bin/esphome run docs/esphome-probes.yaml
```

It refuses two roles sharing an address, refuses anything that is not sixteen hex
characters, and validates the result with ESPHome before saying it is written. So
the failure modes of hand-editing this are all gone.

Watch it for ten minutes. All three should read within about a degree of each
other and of the room.

**Worth keeping.** The generated file is gitignored because it is per-machine, but
once it holds real addresses it saves redoing the squeezing:

```sh
git add -f docs/esphome-probes.yaml
```

## Step 6: check the signal before anything is permanent

Take the board to where it will actually live, on USB power, and read the RSSI at
`http://hydrosnooze-temp.local`.

| Reading | Verdict |
|---|---|
| better than -65 dBm | fine |
| -65 to -70 dBm | workable, keep an eye on it |
| worse than -70 dBm | move it, or wait for the Seeed board |

The bedroom read **-87 dBm** before the extender went in, which is why this check
is here rather than assumed. These SuperMini boards are known for a weaker antenna
than the Seeed design, and they vary between individual units.

## Step 7: place the probes, and run a night doing nothing

**On the hoses, not in the bed.** A probe taped under a sheet measures one spot,
affected by exactly where it sits, body contact and compression. A probe on the
hoses measures the actual thermal exchange, which is a cleaner signal and a more
useful one.

- **water_flow**: on the hose **going to** the bed. This is the water the unit is
  circulating, which is the thing its setpoint actually refers to
- **water_return**: on the hose **coming back**. The same water after the bed has
  had it
- **room**: air temperature, away from the bed, off the floor, not near the
  radiator, the window or the door

### Getting good contact, which matters more than position

A probe resting against a hose reads a mixture of hose and room air, and the room
will win. Two things fix that:

1. **Press the metal tip flat along the hose**, running with it rather than across
   it, and tape it down firmly. More contact area is better
2. **Insulate over the top.** Foam pipe lagging, or a wrap of anything, over the
   probe and a few centimetres of hose either side

Without the insulation the readings will be pulled towards room temperature and
the difference between flow and return, which is the interesting part, will be
squashed towards nothing.

Keep both probes the same distance from the unit, so the comparison between them
is fair.

### Then leave it alone for one night

No service changes, nothing reading it. Just look at the numbers in the morning.

That night is what tells us what the numbers really do, which is what the
mode-switching thresholds have to be set against.

## What the two water probes tell you

This is the part that makes hoses better than a pad probe.

**Flow should track the setpoint.** It is the water the unit circulates, and the
setpoint is a statement about that water. So this probe is a direct check on
whether the unit did what infrared told it to, which nothing in this project could
do before. Expect a lag of a few minutes and some offset, but they should move
together.

**Return minus flow is the heat actually moving.**

| | What it means |
|---|---|
| return **warmer** than flow | the bed is putting heat into the water, so it wants cooling |
| return **colder** than flow | the water is giving heat up to the bed |
| return **equal** to flow | nothing is moving, the bed is at temperature |

That difference is the signal the cooling-priority rule needs, and it is better
than a pad probe would give because it does not depend on where anything was
taped or whether someone is lying on it.

It should also agree with the plug. A big difference between flow and return means
the unit is working hard, which means a large draw. Two independent measurements
of the same event, which is the most useful kind to have.

## When the Seeed board arrives

Add one flag. Do not edit the file: this script rewrites it, so a hand-edited line
would be lost the next time it runs.

```sh
./scripts/probes.py --seeed \
  --flow 0x... --return 0x... --room 0x...
```

Reflash, and everything else carries over. The probes, their addresses and their
roles are all unchanged, because none of that belongs to the board. Nothing done
here is wasted, and the spare SuperMini becomes the bench board.
