# Moving the bedroom onto the hub

Three devices live in the bedroom and all three were on a booster: the blaster,
the plug and the probe board. This is how I move them onto the hub instead, and
how I tell whether it helped.

**The evidence that this is worth doing.** On 19 September I put the Pi on an
ethernet cable and switched its own Wi-Fi off. At 21:33 the blaster and the probe
board still dropped, together, four hours to the second after the drop before it.
The Pi was never the cause. Neither was signal strength: the probe board reports
-35 to -53 dBm, which is excellent. Something in the booster path knocks
everything over on a four hour timer.

The marks are **01:33, 05:33, 09:33, 13:33, 17:33 and 21:33**, so an answer never
takes more than four hours to arrive.

---

## What the configuration does now

Both boards list **two networks** rather than one, with the hub preferred:

```yaml
wifi:
  networks:
    - ssid: !secret wifi_ssid_hub     # VM1876778,     the hub
      password: !secret wifi_password
      priority: 10
    - ssid: !secret wifi_ssid         # VM1876778_EXT, the booster
      password: !secret wifi_password
      priority: 0
```

`priority` is the line that matters. Without it ESPHome takes whichever network
is loudest, and that is always the booster three metres away. With it, the hub
wins whenever it can be heard at all.

**The booster staying listed is what makes this safe.** If the hub cannot be
reached from behind the bed the board lands back on the booster instead of
disappearing and needing a USB cable and a torch.

And both boards now publish which one they chose:

```yaml
text_sensor:
  - platform: wifi_info
    ssid:
      name: "wifi_network"
```

Before this the only way to answer "which access point is it actually on" was a
serial cable and a boot banner, which is a poor way to run an experiment whose
entire question is exactly that.

---

## Step 1: add the hub to the secrets

`docs/secrets.yaml` is gitignored and lives only on the Mac. It needs one new
line, and **ESPHome will refuse to compile without it**:

```yaml
wifi_ssid: "VM1876778_EXT"
wifi_ssid_hub: "VM1876778"
wifi_password: "the same password as before"
```

One password covers both. A booster repeats its hub, so they share it.

Check the hub's name on my phone's Wi-Fi list first. Both should be visible from
the bedroom, and if the hub is not listed at all then it cannot be reached from
there and there is no point going further: that is the answer, and it means an
access point in the room rather than a different network.

---

## Step 2: the probe board first

Deliberately not the blaster. A night runs perfectly well without temperatures,
and it runs not at all without the blaster. If something about this is wrong I
would rather find out on the device that cannot ruin a night.

```sh
cd ~/Documents/GitHub/Hydro-Snooze-app
git pull
./scripts/probes.py --keep
~/esphome/bin/esphome run docs/esphome-probes.yaml
```

`--keep` reuses the three probe addresses already in the file, so nothing about
the probes or the buttons changes. Choose **Over The Air** when it asks.

It flashes over the old network, reboots, and picks a network by priority.

---

## Step 3: read the answer before going any further

```sh
ssh liam@hydrosnooze.local 'curl -s http://127.0.0.1:8000/api/health'
```

The probes row now names the access point:

```
flow 20.1C, return 20.5C, room 19.8C. nothing moving. Signal -53 dBm on VM1876778
```

Three outcomes, and only one of them means carry on:

| What it says | What it means | Next |
|---|---|---|
| `on VM1876778`, better than about -70 dBm | The hub is reachable and strong | Carry on to step 4 |
| `on VM1876778`, worse than about -75 dBm | Reachable but marginal. An ESP32 antenna is several dB worse than a phone, so this is as good as it gets | **Stop.** Buy the powerline kit with a built-in access point |
| `on VM1876778_EXT` | The hub could not be heard at all and it fell back | **Stop.** Same answer, and nothing has broken |

The last two rows are not failures of the experiment. They are the experiment
answering.

---

## Step 4: the blaster

Only once step 3 came back clean.

```sh
~/esphome/bin/esphome run docs/esphome-hydrosnooze.yaml
```

Over the air again. This board has no sensors, so the service never sees a
reading from it and the health row cannot report its network. Its own page can:

```
http://hydrosnooze-ir.local
```

Look for `wifi_network`. Then prove it actually works rather than merely
answering, because those are different things and infrared is one way:

```sh
curl -X POST http://hydrosnooze.local:8000/api/power/press
```

Stand where I can see the unit. If it does not react, check the aim of the
blaster before anything else.

---

## Step 5: the plug, last

The Shelly has no fallback network. If it cannot join the hub it drops to its own
access point and has to be found from a phone, which is recoverable but tedious
at bedtime. So it goes last, once the boards have already proved the hub is
strong enough in that room.

```
http://hydrosnooze-plug.local  →  Settings  →  Wi-Fi
```

Change the network to `VM1876778`, save, and wait for it to come back.

```sh
ssh liam@hydrosnooze.local 'curl -s http://127.0.0.1:8000/api/health'
```

All four rows green and nothing else to do.

---

## Step 6: wait for a mark

Nothing to run. Watch the app's event log at the next **01:33, 05:33, 09:33,
13:33, 17:33 or 21:33**.

- **Nothing happens** — the boosters were the problem and this fixed it for free
- **Everything drops together anyway** — it is the hub itself, and the next thing
  to try is fixing its Wi-Fi channel manually and turning off any auto
  optimisation or "Intelligent WiFi" setting
- **Only the plug drops** — the plug is marginal on the hub. Put it back on the
  booster, or move it

---

## If a board does not come back

It should not happen, because the booster is still in the list. If it does:

```sh
~/esphome/bin/esphome logs docs/esphome-probes.yaml
```

That attaches over Wi-Fi without recompiling, so it only works if the board is on
a network at all. If it is not, the board needs the USB cable and the same
`esphome run` command with the serial port chosen instead of Over The Air.

**Worth doing this in the afternoon rather than at bedtime.** The board is behind
the bed with the probes and the three buttons wired to it, and getting a laptop
to it is a five minute job in daylight and a bad half hour at eleven at night.

---

## The traps, in one place

| | |
|---|---|
| Missing `wifi_ssid_hub` | ESPHome refuses to compile. Add it to `docs/secrets.yaml` before anything else |
| Editing the generated file | `docs/esphome-probes.yaml` is written by `scripts/probes.py`. Edits there vanish on the next run |
| Flashing the blaster first | A night runs without probes and not at all without the blaster. Probe board first, always |
| No `priority` | ESPHome takes whichever network is loudest, which is always the booster. The hub never wins |
| Moving the plug early | It has no fallback. If it cannot join it goes to its own access point and has to be rescued |
| Trusting the dot | Green on the blaster means the board answered the network. Only the plug can confirm a press reached the unit |
