# Withings fixtures

**Invented.** Nobody slept these nights. This repository is public, so no real
night off the mat goes in it.

What is real is the shape. The files have the same keys, the same types and the
same key order as seven real nights captured on 24 September, and they obey every
rule those nights obeyed, from `night_events` being JSON inside a string down to
where the gaps fall. [docs/withings.md](../../../../docs/withings.md) has the rules.
The values are made up: dates, times, heart rate, breathing and sleep.

| File | What it is |
|---|---|
| `getsummary.json` | One `getsummary` response holding all four nights |
| `get-<date>.json` | The `get` response for that night |

| Night | Why it is here |
|---|---|
| 2026-10-22 | Plain. No trips out of bed, awake a while before getting up |
| 2026-10-23 | A trip at 03:40, so the night exists hours before the morning |
| 2026-10-24 | Two trips, the second 35 minutes, back into bed after the first already asleep, and still asleep at the end, so `wakeup_latency` is 0 |
| 2026-10-25 | The clocks go back. The night runs through 01:00 to 02:00 twice, with a trip in the second |

Written by `./scripts/withings-fixtures.py`, which is seeded, so running it again
writes the same bytes. Change the script and run it rather than editing these by
hand: it refuses to write a night that breaks a rule, and a hand edit gets no such
check.

`./scripts/withings-fixtures.py --check <capture folder>` holds a real capture to
the same rules.
