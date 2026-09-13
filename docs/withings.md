# The Withings Sleep Analyzer

The bed has never known anything about me. It knows how many watts the unit draws,
and since the probes went in it knows how warm the mattress is, but the whole
schedule runs on a guess about when I sleep and how I sleep. A Sleep Analyzer under
the mattress is the first measurement of the sleeper rather than the machine.

Everything below was established on **13 September 2026**, most of it against the
live API rather than from reading. Where something is documented but unproven I say
so, because this project has already been bitten twice by an inference wearing the
costume of a fact.

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

Deep, REM and awake line up with three of the four stages the schedule already
runs, which is convenient but coincidental. These are measurements, not targets,
and nothing here is ever allowed to drive the unit.

The summary object's own keys:

```
id  startdate  enddate  date  timezone  hash_deviceid
model  model_id  created  modified  completed  data
```

`data` holds every requested field. `modified` is what `lastupdate` compares
against. `completed` looks like the "has Withings finished with this night" flag,
though I have not proven that.

### Three traps in the parser

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

The loop is gated on `_clock_ok`. `expires_at` and `lastupdate` are both unix
timestamps, so a Pi back from a power cut with the wrong time refreshes a token
that has not expired and asks for every night since 1970. It has to wait for NTP
before it does anything.

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

Everything above the line was measured or read. These were not.

**`night_events` came back null.** Requested, and explicitly null rather than
absent, on the demo account's Aura Sensor V2. That is the field that carries when I
got into bed, fell asleep, woke and got out, and it is the entire basis for ever
anchoring the Drift stage to when I actually fell asleep. It may be populated by a
Sleep Analyzer, which is newer hardware, and the specification documents it with no
device restriction. But it is now a risk rather than an assumption, and it is the
first thing to check on my own first night.

**Which scope carries sleep data.** `user.activity` was granted and the demo
account returned nights, but the demo account also had no recent data, so this has
not been isolated. Three independent projects say `user.activity`. Not proven here.

**Whether the intervals tile a night without gaps.** One entry tells me the shape,
not the coverage.

**Which optional fields a UK Sleep Analyzer populates.** The Aura Sensor V2 did not
return `mvt_score`, `hrv_quality`, `withings_index` or `breathing_sounds`. The
specification says `mvt_score` is EU Sleep Analyzer only, so it should appear on
mine. If it does and `night_events` is still null, that tells me the null is
deliberate rather than a device limitation.
