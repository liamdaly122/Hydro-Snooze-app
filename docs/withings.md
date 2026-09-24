# The Withings Sleep Analyzer

The bed has never known anything about me. It knows how many watts the unit draws,
and since the probes went in it knows how warm the mattress is, but the whole
schedule runs on a guess about when I sleep and how I sleep. A Sleep Analyzer under
the mattress is the first measurement of the sleeper rather than the machine.

Everything below was established on **13 September 2026**, most of it against the
live API rather than from reading. What a real night looks like was settled on
**24 September**, against seven nights on my own mat. Where something is documented
but unproven I say so, because this project has already been bitten twice by an
inference wearing the costume of a fact.

**The bed never depends on any of this.** The integration runs in its own loop, in
its own module, and never takes the command lock. An internet outage must be a
missing chart, never a cold bed.

---

## Where the credentials live

The application is registered at `developer.withings.com/dashboard/`, in the
**Development** environment, and it stays there.

The client ID and secret are in my password manager and in `.env`, nowhere else.
`.gitignore` already covers `.env`, `.env.*` and `.env~`, the last two because a
`nano` backup got committed once.

| Where | File |
|---|---|
| The Pi | `/opt/hydrosnooze/.env`, `chmod 600` |
| This Mac, for `scripts/dev.sh` | `backend/.env` |

---

## The redirect URIs

Three are registered, because which one is right depends on where the browser is,
not where the server is:

| URI | Browser must be on |
|---|---|
| `http://hydrosnooze.local:8000/api/withings/callback` | any device on my Wi-Fi |
| `http://localhost:8000/api/withings/callback` | this Mac, running `dev.sh` |
| `http://127.0.0.1:8910/callback` | this Mac, running the spike |

**Plain HTTP, a `.local` hostname and port 8000 are all fine**, with one condition
that Withings states plainly:

> HTTP URLs, localhost and ports other than 80 or 443 are not supported for
> applications running in production. You may use them during the integration
> phase but your application will be restricted to 10 users.

One sleeper, two at the very most. The cap never binds, so the application stays in
Development permanently and no HTTPS callback is ever needed.

### The dashboard's Test button fails, and that is correct

Pressing **Test** returns `Partner error: Host Unreachable` for all three. It is
testing the wrong thing for our purposes. That field serves two masters: OAuth
redirect URIs and data notification callbacks. A notification callback really does
have to be reachable from Withings' servers, because they POST to it. A redirect
URI does not: the redirect is a 302 sent to *my browser*, which is on my own
network.

**Confirmed against the live API.** Authorising with `redirect_uri=http://127.0.0.1:8910/callback`
returned a working token. Withings does not enforce reachability at authorise time.

This is also the clearest argument for V1 polling rather than subscribing to
notifications. A webhook would need a publicly reachable HTTPS endpoint, which the
Pi does not have and should not have.

---

## The token exchange is not signed

This cost the most time to settle, so here is the whole answer.

The OpenAPI specification gives `requesttoken` **two alternative request bodies**:

| Body | Parameters |
|---|---|
| Using client secret | `action`, `client_id`, `client_secret`, `grant_type`, `code`, `redirect_uri` |
| Using signature | `action`, `client_id`, `nonce`, `signature`, `grant_type`, `code`, `redirect_uri` |

They are alternatives, not a rule and an exception. That is why two Withings
documents appeared to contradict each other and why four client libraries run
unsigned without trouble. Nobody was wrong.

**We use the client-secret body.** Proven on the live API: `requesttoken` returned
`status=0` on the first attempt, unsigned, with `expires_in: 10800`.

`getnonce` and HMAC-SHA256 signing are not needed anywhere in this integration.
`/v2/signature` and the Logistics API are for contracted partners.

```
https://account.withings.com/oauth2_user/authorize2   consent, in the browser
https://wbsapi.withings.net/v2/oauth2                 action=requesttoken
https://wbsapi.withings.net/v2/sleep                  action=get, action=getsummary
https://wbsapi.withings.net/v2/user                   action=getdevice
```

Every call is a `POST` with `Content-Type: application/x-www-form-urlencoded`, and
every response is `{"status": n, "body": {...}}`.

### Tokens

| | |
|---|---|
| Authorization code | **30 seconds.** The callback must exchange it immediately, which rules out any copy-and-paste flow |
| Access token | 3 hours (`expires_in: 10800`) |
| Refresh token | 1 year, rotated on every refresh |
| Old refresh token after rotation | invalid 8 hours later |

That eight-hour grace is more forgiving than I first assumed, but it does not
remove the failure. **Write the new pair and commit it before using the new access
token, never the other way round.** A Pi that dies in that window and stays off for
a day comes back permanently disconnected, and nothing short of me re-authorising
by hand fixes it.

### Testing without a device

`&mode=demo` on the authorize URL authorises a Withings demo account. It needs
nothing signed. This is what let the whole auth path be proven before the mat
arrived.

Not to be confused with the `getdemoaccess` endpoint, which is a different thing
and does require a nonce and signature.

### Capturing real nights

`./scripts/withings-capture.py` on the Mac. One sign-in through the `127.0.0.1:8910`
redirect, then every `getsummary` and `get` response for the last week written to
`backend/data/withings/` byte for byte, which git never sees. Tokens are never
saved. It prints what the responses settled in structure and durations, never
vitals, which is the part that is safe to paste anywhere.

### Testing against invented nights

The repository is public, so the tests never see a real night. `./scripts/withings-fixtures.py`
writes four invented ones into `backend/tests/fixtures/withings/`, with the same keys,
types and key order as the real seven and every rule below built in. It refuses
to write a night that breaks one. `--check <capture folder>` holds a real capture
to the same rules, so a rule that stops being true gets noticed.

---

## Fetching sleep

Two calls, and they are limited in different ways.

**`action=getsummary`** finds nights. Takes either `startdateymd` + `enddateymd`
**or** `lastupdate`, never both. The loop uses `lastupdate`, storing the high-water
mark between passes.

**`action=get`** fetches the detail of one night, using the `startdate` and
`enddate` that the summary gave for it. These are unix timestamps, not `ymd`.

> If your input `startdate` and `enddate` are separated by more than 24h, only the
> first 24h after `startdate` will be returned.

So `get` is **one night per call, always**. It does not paginate. It truncates,
silently.

### Pagination, and the mistake that is easy to make

`getsummary` paginates at 300 nights a page. Measured over 741 demo nights:

```
page 1   300 nights   more=True    offset=300
page 2   298 nights   more=True    offset=600      not 598
page 3   143 nights   more=False   offset=0
```

Two things to take from that.

**Use the `offset` the server returns. Never compute it from how many rows came
back.** Page 2 returned 298 rows and the offset still advanced by 300, so rows get
filtered server-side after the page is cut. Adding up row counts would have
re-read or skipped nights and produced a result that looks perfectly fine.

**Loop on `more`, never on `offset`.** The final page came back with `more=False`
and `offset=0`. A loop that treats a non-zero offset as its condition runs forever.

### Rate limit

Documented as no more than one poll per ten minutes per user. The loop runs every
thirty, which is comfortably inside it.

---

## What a night actually looks like

One real series entry from `action=get`:

```json
{"startdate": 1680467403, "enddate": 1680467523, "state": 0,
 "hr":                  {"1680467403": 70, "1680467463": 70},
 "rr":                  {"1680467403": 15, "1680467463": 16},
 "snoring":             {"1680467403": 0,  "1680467463": 0},
 "sdnn_1":              {"1680467403": 5,  "1680467463": 5},
 "rmssd":               {"1680467403": 5,  "1680467463": 5},
 "chest_movement_rate": {"1680467403": 15, "1680467463": 16},
 "hash_deviceid": "...", "model": "Aura Sensor V2", "model_id": 63}
```

Each entry is **one sleep-state interval carrying its own metrics**, and each
metric is a map from unix timestamp to value. Samples are **60 seconds apart**. A
nine-hour night came back as 110 intervals of varying length.

**The demo account was the same hardware as mine.** Every interval off my own mat
also says `"model": "Aura Sensor V2"`, `"model_id": 63`. Aura Sensor V2 is what
Withings calls the Sleep Analyzer internally. So whatever the demo nights lacked,
they lacked because of the demo data, not because of the device.

Sleep states:

| Value | Meaning |
|---|---|
| 0 | awake |
| 1 | light |
| 2 | deep |
| 3 | rem |
| 4 | manual |
| 5 | unspecified |
| 15 | out of bed (needs a specific plan) |

My mat only ever sent 0 to 3. Time out of bed is not a state 15 interval, it is a
**gap between intervals**. See below.

Deep, REM and awake line up with three of the four stages the schedule already
runs, which is convenient but coincidental. These are measurements, not targets,
and nothing here is ever allowed to drive the unit.

The summary object's own keys:

```
id  startdate  enddate  date  timezone  hash_deviceid
model  model_id  created  modified  completed  data
```

`data` holds every requested field. `modified` is what `lastupdate` compares
against. `completed` is a boolean, and it was `true` on all seven real nights, but
all seven were captured after the fact. Whether it is `false` while a night is
still going is not proven.

### Seven traps in the parser

**The timestamp keys are strings.** JSON object keys always are. `int()` every one
on the way in, or a join against `power_samples` compares `"1680467403"` to
`1680467403`, matches nothing, and returns a tidy empty result that looks like a
quiet night.

**`hash_deviceid` is on every entry**, not once per file. 110 intervals carry it
110 times. Anything that strips it before a fixture is committed has to hit all of
them, and then grep for the real value to prove none survived.

**Unsupported fields vanish without complaint.** Ask for something the device does
not do and it is simply absent from the response, with no error. Absent and null
both mean "not available" and neither may ever become a zero.

**`night_events` is JSON inside a string.** Not an object: a string that has to be
decoded a second time. What is inside it is set out in the next section. A parser
that reads it as a plain value gets text and nothing useful out of it.

**`model` means two different things.** On a summary, and at the top of a `get`
body, it is the number `32`. On every interval it is the name `"Aura Sensor V2"`.
`model_id` is `63` in all three places. Read `model_id`, never `model`.

**An interval is not a stage.** On real nights about four in five neighbouring
intervals have the same state. Withings cuts the time in bed into whole minutes,
one to ten at a time and usually two, whatever the sleep is doing. My nights had
about twenty stage changes each and came back as 123 to 147 intervals. Merge runs of the same state
before calling anything a stage, and never count intervals as if they were one.

**Zero heart-rate variability means no reading.** `sdnn_1` is 0 on 116 of 4,059
minutes, 113 of them awake, and `rmssd` is 0 on every one of those too, plus six
more. Nobody has 0 ms of variability, and moving about is what stops it being
measured, so parse a 0 in either as None. This one is my inference rather than
something Withings says. `hr`, `rr` and `chest_movement_rate` were never 0.

---

## What seven real nights settled

Seven nights off my own mat, 16 to 23 September. Everything in this section was
checked against all seven, not read off one.

**`user.activity` on its own is enough.** Signed in asking for nothing else, was
granted exactly that, and every sleep call answered. The backend asks for this and
nothing more.

### `night_events` is there, and it is the whole night

The field that came back null on the demo account, and the only thing the Drift
stage could ever be anchored on. It is populated on every real night. Decoded, it
looks like this, from an invented night with the same shape as mine:

```json
{"1": [0, 25500], "2": [1200, 24600], "3": [24900, 3600], "4": [25200, 3600]}
```

| Key | Event |
|---|---|
| 1 | got into bed |
| 2 | fell asleep |
| 3 | woke up |
| 4 | got out of bed |

**Each list is gaps, not times.** The first number is seconds after the summary's
`startdate`, and each one after it is seconds after the previous event *of the same
kind*. Add them up per key, then add `startdate`. The example reads: in bed at 0,
asleep at 1200, awake at 24900, out at 25200, back in at 25500, asleep at 25800,
awake at 28500, out at 28800.

Those four meanings are my reading, not Withings' words, so here is why I trust
them. Decoded that way, on all seven nights:

- the first "fell asleep" is exactly `sleep_latency`
- the night's length minus the last "woke up" is exactly `wakeup_latency`
- the asleep spans add up to exactly `total_sleep_time`, and the awake spans
  between them to exactly `waso`
- "woke up" happens `wakeupcount` + 1 times, the last one being the morning
- each "out of bed" to the next "into bed" is exactly one of the gaps between the
  `get` intervals, and there are `out_of_bed_count` of them

Nineteen checks, seven nights, no exceptions.

### The intervals cover the time in bed, and nothing else

The `get` intervals start and end exactly on the summary's `startdate` and
`enddate`, never overlap, and leave a gap only where I was out of bed. So there are
three kinds of time, and the join has to keep them apart:

| | How it shows |
|---|---|
| asleep | an interval in state 1, 2 or 3 |
| awake in bed | an interval in state 0 |
| out of bed | no interval at all |

Summed per state, the intervals match `lightsleepduration`, `deepsleepduration`,
`remsleepduration` and `wakeupduration` to the second, and `total_timeinbed` is the
night's length minus the gaps. The summary is arithmetic on the intervals, which
makes the pair of them check each other and makes them a good fixture.

Every metric has one sample per minute in bed, all on the same minutes, each one
inside its own interval: from `startdate`, up to but not including `enddate`.

### What a UK Sleep Analyzer fills in

| | Came back | Did not |
|---|---|---|
| `getsummary` | 27 of the 29 fields asked for, plus `breathing_quality_assessment` unasked | `asleepduration`, `withings_index` |
| `get` | `hr`, `rr`, `snoring`, `sdnn_1`, `rmssd`, `hrv_quality`, `mvt_score`, `chest_movement_rate` | `withings_index`, `breathing_sounds` |

Everything that did not come back was absent, not null. `snoring` was 0 on every
one of 4,059 minutes, which is either true or a sensor that never fires. Nothing
here can tell those apart.

**`chest_movement_rate` is `rr`**, value for value, on every minute of every night.
Two names, one measurement, so never treat them as two.

**The summary's heart rate is over sleep only.** `hr_min` and `hr_max` are the
lowest and highest per-minute `hr` while asleep, on all seven nights. `hr_average`
is within a beat of the sleeping mean but not exactly it, so it is not something
to recompute. `rr_min` and `rr_max` are over every minute in bed.

### When a night turns up

**A night exists a minute or two after I first get out of bed**, not when the
morning comes. On all seven, `created` was 69 to 110 seconds after the first "out
of bed". A 4am trip to the bathroom creates the night hours early, and it then
stretches every time I get back in: `enddate` moves and the intervals grow. On a
morning with no trip, that puts the night in the API about two minutes after I get
up, well inside the twenty the morning report waits.

**It is modified again 9 to 22 hours later**, five of the seven times during the
following night.

So a night seen once is not a night finished. The loop has to store each one
against its `id` and replace it every time `lastupdate` hands it back, never append
it. That the `id` stays the same while a night grows is an assumption: one capture
cannot show it.

---

## Errors

The specification shares families rather than exact causes.

| Status | Meaning | What the loop does |
|---|---|---|
| 0 | success | proceed |
| 100..102 | authentication failed | refresh once, then see below |
| 200 | authentication failed | refresh once |
| 201..213 | invalid params | our bug, log it |
| 214, 277 | unauthorized | stop, needs reconnecting |
| 601 | too many requests | back off an hour |
| 5xx | timeout | retry next pass |

**Status 100 is genuinely ambiguous.** `llms.md` calls it "succeeded but no data
found". The OpenAPI specification puts `100..102` under authentication failures.
The specification wins, because `llms.md` says it does, but rather than pick a side
the loop handles both: on 100, refresh once and retry, and if 100 comes back again
treat it as no data and **do not** mark the account for reconnection. That is
correct under either reading and costs one wasted call on a night I slept
elsewhere.

Observed, separately: an empty result came back as `status=0` with an empty
`series`, not as 100. That leans towards the specification being right.

### The clock

`expires_at` and `lastupdate` are both unix timestamps, so a Pi back from a power
cut with the wrong time refreshes a token that has not expired and asks for nights
that have not happened. It has to wait for NTP before it does anything.

**Not on `_clock_ok`, though, which was the plan.** That one gives up after ten
minutes and runs on whatever clock it has, which is right for the bed, because no
night at all is worse than a night at the wrong hour. Nothing here is worth that.
The loop asks `clocksync.synchronised()` itself and waits for as long as it takes.
A Pi with no NTP most likely has no internet either, so the wait costs nothing.

---

## What is built

Everything lives in `backend/hydrosnooze/withings/`, with its routes in
`backend/hydrosnooze/api/withings.py`:

| | |
|---|---|
| `client.py` | Talking to Withings and nothing else. Pages on `more`, takes the server's offset |
| `parse.py` | Nights, stages and minutes, with every trap above stepped round |
| `sync.py` | The loop, every half hour |
| `health.py` | The Health Report, built on request from what is stored |

| Route | |
|---|---|
| `GET /api/withings` | Where the connection is up to |
| `GET /api/withings/connect` | Sends the browser to sign in |
| `GET /api/withings/callback` | Where Withings sends it back. Trades the code in at once |
| `POST /api/withings/sync` | Fetch now, at most every ten minutes |
| `DELETE /api/withings` | Disconnect. The nights stay |
| `GET /api/health-report?date=` | One night as the Health Report draws it, and its week |

**The rule that outranks the rest holds in code, with a test for each half.** The
loop is its own task and never takes the command lock: a test holds the lock and
fetches anyway. Nothing it says can reach the phone at 3am: a test checks its
warning against the notifier. Every failure ends as a line of text on the Health
Report and nothing else.

Four decisions that are not obvious from the code alone:

- **Tokens are written with a full sync to disk**, and before the new access
  token is used. The rest of the database runs `synchronous=NORMAL`, which can
  lose a commit in a power cut. Losing this one would mean signing in by hand
- **It keeps real time, not the service clock.** The simulator runs that at up to
  120 times real speed, and "every half hour" would become every fifteen seconds
  against Withings
- **A night is replaced whole every time it changes**, matched on its start as
  well as its id, so a night whose id changed as it grew cannot be counted twice
- **A refused refresh says reconnect once and keeps trying every pass.** An old
  refresh token stays good for eight hours after a rotation whose answer never
  arrived, so a refusal can pass by itself

To connect: put `HS_WITHINGS_CLIENT_ID` and `HS_WITHINGS_CLIENT_SECRET` in `.env`,
restart, open the app at `http://hydrosnooze.local:8000` and press Connect. The
first pass fetches the last month.

---

## Two sources, and which one wins

| Source | Use it for |
|---|---|
| `developer.withings.com/openapi.yaml` | **The authority.** Every parameter, type and response schema |
| `developer.withings.com/llms.md` | A readable overview, written for coding agents |

`llms.md` says the specification is authoritative, so where they disagree the
specification wins. It has been wrong once already, on status 100.

---

## Still unknown

This section used to hold four questions: `night_events`, the scope, whether the
intervals cover the night, and which fields come back. Seven real nights answered
all four, above. These are what is left, and none of them has been measured.

**Whether `completed` is ever false.** All seven nights were captured after the
fact. One capture in the small hours, after getting up and before getting back in,
would show what an unfinished night looks like.

**Whether a night's `id` survives it growing.** The same capture would show that.

**What 255 means in `mvt_score`.** It is the top value, seven times, all awake,
sitting on a spread that runs up to 245. That reads as the ceiling of the scale
rather than a code for "no reading", but it is a guess.

**The 5xx row in the error table.** `aiowithings`, the client Home Assistant uses,
treats only 522 as a timeout and 524 as a bad state, and files most of 501 to 533
as invalid parameters or other errors. It also counts 401 as an authentication
failure and 2553 to 2555 as unauthorised. Check that against `openapi.yaml` before
the loop's error handling is written.
