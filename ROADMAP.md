# HydroSnooze: from the concept to a working unit

This is the roadmap to a unit that cools the bed by itself every night.

**Steps 1 to 3 are done.** The whole thing works end to end against a simulated unit: the service,
the scheduler, every button sequence, and the app driving all of it. What is left is the hardware
and the swap.

---

## Where I am now

| Done | Not done |
|---|---|
| Every screen, working on the phone against the real service | Anything touching real hardware |
| A simulated HS1001 with every documented quirk | The eight captured infrared codes |
| Every button sequence, with 96 tests behind them | Real power readings from the plug |
| The scheduler, the database, the live feed | The Pi it will eventually live on |
| A clock that can be jumped to 21:29 to watch an evening | |

Run it with `./scripts/dev.sh`.

## The idea that makes the rest of this easy

Build the real service now, but plug it into a **pretend unit** instead of a real one. The pretend
unit prints out every button press it would have sent, so I can read them and check they are right.

When the hardware arrives, two lines of config change from `fake` to `esphome` and `shelly`, and the
same code starts talking to the real thing. Nothing gets built twice.

The pretend unit is not a throwaway either. It stays forever as the way to test changes without
disturbing the bedroom.

## Two tracks, and they do not wait for each other

**Software** happens at the Mac and needs no hardware.
**Hardware** happens in the room with my hands. Only I can do those.

They only meet at Step 9. So I can order the parts today and do the hardware evenings whenever
suits, while the software gets built in parallel. Realistically the app will be finished before the
£11 blaster turns up.

---

# Track one: the software

## Step 1. Build the pretend unit ✅

Code that behaves like an HS1001. It knows the display goes dark after five minutes and swallows the
first two presses. It knows the temperature buttons do nothing at all while the sleep schedule is
running. It knows the three cooling modes cycle round and wrap. Next to it, a pretend plug that
reports believable watts based on what the pretend unit is doing.

Everything it knows comes from the manual and my own testing. Where something is a guess rather than
a fact, the code says so in a comment, so it can be corrected once I can test it for real.

**Done when:** I can tell it to press buttons and it reacts the way the manual says it should.

## Step 2. Write the button sequences ✅

The actual recipes: power on, set the mode, set a temperature, arm the schedule, write the schedule.
Each one prints every press as it goes, like this:

```
21:30:00.0  set_temperature(19)
21:30:00.0  -> temp_down   wake 1/2      SWALLOWED, display was dark
21:30:00.3  -> temp_down   wake 2/2      SWALLOWED, entered adjust mode
21:30:00.6  -> temp_down   rail 1/25
...
21:30:08.4  -> temp_up     4/4
21:30:11.4  unit: ON  quiet  target 19C
```

**Done when:** asking for 19°C prints two wake presses, twenty five down presses and four up
presses, and the pretend unit lands on exactly 19. Automated tests check this from every possible
starting state, so I know the counts are right before a single infrared photon is ever emitted.

This is the step that de-risks the whole project. Counting presses wrong is the easiest mistake to
make here and the hardest to spot at 3am.

## Step 3. Build the service and connect the app ✅

Three things at once:

- **The scheduler.** Works out 21:30 and 22:00 from my wake time, and fires at those moments.
- **The memory.** A small database holding the schedule, the event log and the power history.
- **The join.** The app stops using invented data and starts talking to this. One line changes.

Plus a **time machine**: a control that jumps the clock to 21:29 so I can watch the whole evening
happen in a few seconds rather than waiting until bedtime. This turns out to be the most useful
debugging tool in the project.

**Done when:** I open the app, set a wake time, jump the clock, and watch the pre-cool and arming
sequences run press by press.

**At this point the app is finished.** It genuinely works, end to end. It is just talking to
something imaginary.

Done. `./scripts/dev.sh`, then the spanner tab, jump to 21:29 and set the speed to 60x.

---

# Track two: the hardware

## Step 4. Buy the parts

Today, so the post has time to happen.

| Item | Roughly |
|---|---|
| Small always-on computer, a Pi Zero 2 W or a Pi 4 with 2GB | £35 to £70 |
| XIAO Smart IR Mate | £11 |
| Shelly Plug S Gen3 | £18 |
| A decent A2 SD card, or a small USB SSD | £10 to £20 |

Do not cheap out on the card. This thing runs day and night writing logs, and a cheap card quietly
failing is the single most likely way the whole project stops working.

## Step 5. Set up the Pi

One evening, and easier than it sounds. **`SETUP.md` has all of steps 5 to 10 in full detail.**

Download Raspberry Pi Imager onto the Mac and choose Raspberry Pi OS Lite (64-bit). **Before
writing, click the settings gear.** Set the hostname to `hydrosnooze`, turn on SSH, set a username
and password, and type in my Wi-Fi name and password.

That gear step is the one that makes this painless. It means the Pi joins the network by itself the
first time it boots, with no screen, keyboard or mouse attached.

Write the card, put it in, power it on, wait two minutes.

**Done when:** `ssh liam@hydrosnooze.local` from the Mac gives me a prompt.

## Step 6. Get the blaster working and capture the codes

One evening. This is the important one.

Plug the XIAO into USB power. Install ESPHome on the Pi and adopt the device. Get the real pin
numbers from the vendor's published configuration on GitHub rather than guessing at them. Then flash
a listening configuration, point the remote at the blaster from about 10cm, and press each of the
eight buttons in turn while watching the log.

**Done when:** I have all eight codes written down. If they come out as a named protocol such as NEC
with an address and a command, that is far better and far more reliable than raw timings.

Everything else hangs on this step. If the codes capture cleanly, the rest is straightforward.

## Step 7. Answer the one remaining question

Ten minutes, once the blaster can fire presses on demand.

Enter the schedule setup, press the schedule button once more to reach Phase 2, then wait twenty
seconds without touching anything. Does it arm itself with my saved temperatures?

This does not block anything. It only changes how much the app should trust a single attempt at
arming, and whether arming is genuinely self-correcting when a press gets dropped.

## Step 8. Plug in the Shelly

About half an hour.

Put it between the wall and the unit, connect it to Wi-Fi, and write down its IP address. Then note
the watts in four states: off, on but sitting at temperature, cooling, and heating.

Those four numbers are how the app knows whether a power command actually worked. Until I measure
them they are guesses based on the manual's 170W and 300W.

---

# Where the tracks meet

## Step 9. Feed in the real numbers and flip the switch

From what I collected in Steps 6 to 8: the eight captured codes, the Shelly's IP address, the four
power readings. Then two lines change:

```
HS_TRANSMITTER=fake     ->  HS_TRANSMITTER=esphome
HS_POWER_MONITOR=fake   ->  HS_POWER_MONITOR=shelly
```

Nothing else moves. That is the entire point of having built it this way.

## Step 10. Install it on the Pi

`./scripts/install.sh` on the Pi, then `./scripts/deploy.sh` from the Mac. Then open
`http://hydrosnooze.local:8000` in Safari on the phone and add it to the Home Screen.

The app is still built on the Mac and copied across. The Pi never builds it.

---

# Earning trust before I sleep on it

## Step 11. Daytime tests only

Power on. Power off. Set a temperature. Set a mode. Watch the unit each time. If the presses land,
the codes are good and the hard part is behind me.

## Step 12. Write the schedule while standing in front of it

This is the one that sends about ninety presses in a row and walks the unit through its setup
wizard. The app shows a warning before it starts, because nothing in software can check what
actually got written. I have to watch it step through three phases myself.

## Step 13. One night with my normal alarm still set

Let it run a full night. In the morning, check the event log and the power chart. Did it power on at
21:30, arm at 22:00, and switch itself off at 06:30?

## Step 14. Then trust it

Also set the Shelly's own auto-off timer as an independent backstop, so the unit switches off even
if the Pi is down.

---

# What could go wrong

**The codes might not capture cleanly.** This is the main risk in the whole project, and Step 6
finds out early rather than late.

**The unit might not behave the way the manual says.** The pretend unit is built from the manual and
my notes, so where reality differs I correct the pretend one and the tests catch anything that
breaks as a result.

**The power thresholds are guesses** until Step 8 measures them.

**Nothing can verify what was written to the schedule.** That is a permanent limitation, not a bug.
The app never pretends otherwise, which is why Step 12 involves standing in the room.

# Roughly how long

| | |
|---|---|
| Software | Done |
| Hardware | Three evenings, plus waiting for the post |
| Building trust | About a week of not quite trusting it |

The tracks overlap, so the real answer is that the app will be finished and working against a
pretend unit well before the blaster arrives. If the codes then capture cleanly, I could be done in
an evening.

# One small decision for later

Whether to keep the Vercel copy. Once the real service exists, the Vercel version can only ever show
invented data. It stays useful for looking at design changes on the phone without the Pi being on,
but it does mean keeping the pretend data working. Not urgent, and not a decision I need to make
now.
