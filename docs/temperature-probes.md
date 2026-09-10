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
  --head 0x1c0000031edd2828 \
  --foot 0x3a00000320f18b28 \
  --room 0x9b000003215c4f28

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

- **bed_head**: on top of the pad, under the sheet, at torso height. Not directly
  underneath where I lie, because compression and body contact swamp the reading
- **bed_foot**: the far end or the other side, so the two together say whether the
  bed cools evenly
- **room**: across the room, off the floor, away from the radiator, the window and
  the door

Then **leave it alone for one night**. No service changes, nothing reading it.
Just look at the numbers in the morning.

One night of real readings says more about where the probes should go than any
amount of planning, and it costs nothing but patience.

---

## The thing that will look like a fault and is not

**The probe will not agree with the setpoint.**

The HS1001's setpoint is the temperature of the **water it circulates**. The probe
measures the **surface of the pad**, through a sheet, in a room. Set 24°C and the
probe may well read 26°C, or 22°C.

That is not drift and it is not a broken probe. They are two different
measurements of two different things.

So for the first week the probe is **a new fact, not a check on the old belief**.
Only once there are a few nights of both is the relationship between them
learnable, and only then is it fair to let the app say "the unit did not do what
it was told". The other way round produces alarms about a discrepancy that was
never a fault.

## When the Seeed board arrives

Add one flag. Do not edit the file: this script rewrites it, so a hand-edited line
would be lost the next time it runs.

```sh
./scripts/probes.py --seeed \
  --head 0x... --foot 0x... --room 0x...
```

Reflash, and everything else carries over. The probes, their addresses and their
roles are all unchanged, because none of that belongs to the board. Nothing done
here is wasted, and the spare SuperMini becomes the bench board.
