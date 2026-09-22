# Swapping the probe board for the XIAO

The SuperMini has been the flakiest thing in this project, and on the night of 21
September it went off the network and did not come back. Its name stopped
resolving and nothing answered at its last address, so it was gone rather than
unreachable. The genuine Seeed XIAO ESP32-C3 has been in the drawer since the
start. This is the swap.

---

## What does not change, which is most of it

- **The probe addresses.** A DS18B20's address lives in the probe, not in the
  board. The same three probes move across with the same three serials, so
  `--keep` reuses them and there is no re-identification and no squeezing
  probes in a fist to work out which is which
- **The API key.** Same `hydrosnooze_temp_api_key` from `docs/secrets.yaml`, so
  nothing in the Pi's `.env` is touched
- **The device name.** The new board is still `hydrosnooze-temp`, so
  `HS_PROBES_HOST` is untouched
- **The GPIO numbers.** This is the one that sounds like it should change and
  does not. Both boards are ESP32-C3 and both expose GPIO4 to GPIO7. Only the
  labels printed beside the pads differ

---

## The pins

| What | GPIO | SuperMini says | XIAO says |
|---|---|---|---|
| 1-Wire data, all three probes | GPIO4 | `4` | **`D2`** |
| `button_warmer` | GPIO5 | `5` | **`D3`** |
| `button_cooler` | GPIO6 | `6` | **`D4`** |
| `button_power` | GPIO7 | `7` | **`D5`** |
| Probe red | 3V3 | `3V3` | `3V3` |
| Every ground, probes and buttons | GND | `GND` | `GND` |

`D2` to `D5` are four consecutive pads down one side of the XIAO, with `3V3` and
`GND` on the other side. **Check the silkscreen before soldering.** The
configuration is written in GPIO numbers and does not care what is printed next
to the hole; I am the one who has to get that right.

The 4.7kΩ resistor comes across with the probes: one resistor for the whole bus,
between the joined yellow leads and 3V3. The buttons need no resistors, because
the chip's internal pull-ups do that job.

---

## Before starting

- **Fit the external antenna.** The XIAO ships with one that clips onto the U.FL
  connector. Wi-Fi is the entire reason for this swap, so it would be a strange
  thing to skip
- **A USB-C data cable, not a charge-only one.** A charging cable gives a board
  that powers up and never appears as a serial port, which reads exactly like a
  dead board
- Do this at a table in daylight, not behind the bed at eleven at night

---

## Step 1: turn the old board off, and leave it off

Two boards both calling themselves `hydrosnooze-temp` on one network is an
afternoon I do not want. Unplug it before the new one goes anywhere near power.

## Step 2: move the wiring

The table above. Three probe yellows joined together to `D2`, reds to `3V3`,
blacks to `GND`, one resistor between the joined yellows and `3V3`. The three
buttons each get a core to `D3`, `D4`, `D5` and share the ground with the probes.

## Step 3: build the configuration

```sh
cd ~/Documents/GitHub/Hydro-Snooze-app
git pull
./scripts/secret.py
./scripts/probes.py --keep --seeed
```

`./scripts/secret.py` says whether anything is missing from `docs/secrets.yaml`
before ESPHome finds out in a less helpful way. `wifi_ssid_hub` has to be there.

`--keep` reuses the three addresses already in the file. `--seeed` is how the
swap gets announced, and **it only has to be said once**: the file remembers
afterwards, so plain `./scripts/probes.py --keep` builds for the XIAO from then
on. It prints which board it chose either way.

> `--keep` needs the existing `docs/esphome-probes.yaml`, which is per-machine
> and gitignored. If it has gone, the three addresses are in any old boot log
> from the SuperMini, and failing that stage 1 and 2 of
> [temperature-probes.md](temperature-probes.md) find them again.

## Step 4: first flash, over USB

The board has no firmware and is on no network, so over the air is not an option
this once.

```sh
~/esphome/bin/esphome run docs/esphome-probes.yaml
```

Choose the `/dev/cu.usbmodem...` serial port rather than Over The Air. If it
will not flash: **hold BOOT, tap RESET, release BOOT**, then run it again.

## Step 5: read the boot log

It keeps streaming after the flash. Four things to find, in this order:

```
[gpio.one_wire] Pin: GPIO4
[gpio.one_wire] Found devices:        <- three of them
[gpio.binary_sensor] 'button_warmer'  Pin: GPIO5
[wifi] SSID: 'VM1876778'              <- the hub, not the booster
[wifi] Signal strength: -xx dB
```

Three probes, three buttons, and the hub. Write that signal number down: it is
the first fair comparison between the two boards, measured from the same place
with the same antenna arrangement.

If the SSID reads `VM1876778_EXT`, the hub could not be heard and it fell back to
the booster. Nothing is broken, but that is worth knowing before it goes behind
the bed.

## Step 6: check the Pi sees it

The service on the Pi is running code from before this week, so deploy first or
the health row will not name the network:

```sh
./scripts/deploy.sh liam@hydrosnooze.local
ssh liam@hydrosnooze.local 'curl -s http://127.0.0.1:8000/api/health'
```

The probes row should read something like:

```
flow 20.1C, return 20.5C, room 19.8C. nothing moving. Signal -41 dBm on VM1876778
```

Three temperatures, a signal, and the network it is on.

## Step 7: the buttons

Press each one once, on its own, watching the log. One name per press, one ON and
one OFF:

```
[binary_sensor] 'button_cooler' >> ON
[binary_sensor] 'button_cooler' >> OFF
```

If one press fires two names, the wiring is shorted. [buttons.md](buttons.md) has
the whole story of that particular evening.

## Step 8: put it back and wait for a mark

Behind the bed, and then watch the app's event log at the next **01:33, 05:33,
09:33, 13:33, 17:33 or 21:33**. If the probe board rides through one while the
plug and blaster drop, the SuperMini was the problem and this fixed it.

---

## The traps, in one place

| | |
|---|---|
| Charge-only USB cable | Powers the board and never appears as a serial port. Reads exactly like a dead board |
| Forgetting `--seeed` | Only matters the first time. After that the file remembers and prints which board it built for |
| Editing `docs/esphome-probes.yaml` | It is generated. Edits vanish on the next run. `scripts/probes.py` is the file to change |
| Both boards powered at once | Two `hydrosnooze-temp` on one network. Unplug the old one first |
| `D2` is not GPIO2 | On the XIAO, `D2` is GPIO4. The config speaks GPIO and the silkscreen speaks D |
| No external antenna | The connector is there for a reason and Wi-Fi is why this swap is happening |
| Skipping the deploy in step 6 | The health row only names the access point on this week's service code |
