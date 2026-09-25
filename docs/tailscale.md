# Reaching the bed from anywhere, with Tailscale

The app used to work on the home Wi-Fi and nowhere else. This is how to reach it
from anywhere in the world without putting it on the internet.

**What Tailscale is.** A private network between my own devices. The phone and the Pi
both join it, and from then on the phone can reach the Pi wherever the phone is: on
mobile data, on hotel Wi-Fi, abroad. Nothing is opened on the router and nothing is
visible from the public internet. Anybody who is not signed in to my Tailscale account
cannot even find the address, let alone load it.

**What it is not.** A website. There is no public address, no login page on the
internet for strangers to try, and no copy of anything on someone else's server. The
traffic between the phone and the Pi is encrypted end to end; Tailscale's own servers
only introduce the two devices to each other.

**The rule that outranks the rest still holds.** The bed never depends on any of this.
The scheduler, the bedside buttons and the notifications never go through Tailscale or
the password. If Tailscale goes down, the app cannot be reached from outside the house,
and nothing else changes.

---

## Before starting

- [ ] The Pi is running the version of the app with the password in it. From the Mac:
      `./scripts/deploy.sh liam@hydrosnooze.local`
- [ ] On the Pi, the scripts are up to date too, because the clone is where they run
      from: `cd ~/Hydro-Snooze-app && git pull`

Two things are new in the app, and both are there for this:

- **A password.** One for the house. Each device signs in once and stays signed in for
  six months from the last time it was used, so this is not a thing typed at 3am.
- **A rule about Tailscale.** With no password set, the app answers on the home Wi-Fi
  exactly as it always has, and refuses everything that arrives through Tailscale. So
  setting Tailscale up before the password cannot open anything by accident. It fails
  shut.

---

## Step 1. Set the password, at home

**Do this first.** Everything after it relies on it.

On the Pi:

```sh
cd ~/Hydro-Snooze-app
./scripts/password.py --env /opt/hydrosnooze/.env
sudo systemctl restart hydrosnooze
```

It asks twice. At least ten characters: three or four unrelated words is easy to type
on a phone and hard to guess. Only a hash of it goes into `.env`, so a copy of that
file does not give the password away. The first time, it also writes a key for the
other scripts on the Pi, which cannot type a password.

- [ ] Password set, service restarted
- [ ] On the phone, on the home Wi-Fi, open `http://hydrosnooze.local:8000`. It asks for
      the password. Sign in, and let the iPhone save it to Passwords when it offers
- [ ] Open the menu. At the bottom it says **Signed in on the home network**
- [ ] A notification arrived: "iPhone, Safari signed in on the home network". Every
      sign-in sends one. Sign-ins are rare, so one you did not make is worth knowing
      about straight away
- [ ] The scripts still work. On the Pi: `./scripts/notify.py --env /opt/hydrosnooze/.env --test`
      sends a test notification through the service, which proves the scripts' key
      works

**Forgotten it?** Run `./scripts/password.py` on the Pi again. Nothing else is needed.
Changing the password signs every device out, on purpose.

---

## Step 2. A Tailscale account

Go to [tailscale.com](https://tailscale.com) and sign up. The free Personal plan is
plenty: it covers far more devices than this needs.

It signs in with an existing account rather than a new password: Apple, Google,
Microsoft or GitHub.

- [ ] **Use one that has two-factor sign-in turned on.** That account is now one of the
      two locks on the bed (the app's own password is the other), so it deserves the
      same care as an email account. An Apple ID already has it
- [ ] Signed in to the admin console at `login.tailscale.com/admin`

---

## Step 3. Put the Pi on it

On the Pi:

```sh
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

The first line is Tailscale's own installer: it adds their package repository so the
Pi keeps getting updates with everything else. The second prints a link. Open it on
the Mac, sign in with the same account, and approve the Pi.

Then in the admin console, on the **Machines** page, open the Pi's `...` menu:

- [ ] **Edit machine name**: `hydrosnooze`. This becomes part of the address
- [ ] **Disable key expiry**. Tailscale signs devices out every 180 days by default,
      which is right for a phone and wrong for a Pi nobody is looking at. Without this
      the app quietly stops being reachable from outside half a year from now

And back on the Pi:

- [ ] `sudo tailscale set --auto-update` so it keeps itself up to date
- [ ] `tailscale status` lists the Pi as `hydrosnooze`

---

## Step 4. Give it an https address

The app needs https from outside: the phone will only keep the sign-in cookie on a
secure address, and the app's offline cache only works on one. Tailscale issues a real
certificate for free.

In the admin console, on the **DNS** page:

- [ ] **MagicDNS** is on (it usually is already)
- [ ] Under **HTTPS Certificates**, press **Enable HTTPS**

One thing worth knowing about that second switch. Every https certificate in the world
is listed in public logs, so the address `hydrosnooze.<your tailnet>.ts.net` becomes
publicly known. It is still unreachable to anyone outside the tailnet; knowing a name
is not the same as being able to reach it. It is the reason not to name the Pi after
anything private.

Then on the Pi:

```sh
sudo tailscale serve --bg 8000
tailscale serve status
```

- [ ] `serve status` shows `https://hydrosnooze.<something>.ts.net` handing over to
      `http://127.0.0.1:8000`. Write that address down; it is the app's address from now
      on
- [ ] `--bg` keeps it running after a reboot. Nothing else to set up

**Never run `tailscale funnel`.** Funnel is the other half of the same feature and it
does the opposite of what this guide is for: it puts the address on the public
internet for anyone to load.

- [ ] `tailscale funnel status` says there is no funnel

---

## Step 5. Tell the app

Two lines at the end of `/opt/hydrosnooze/.env` on the Pi, with the real address from
step 4:

```
HS_TUNNEL=tailscale
HS_PUBLIC_URL=https://hydrosnooze.<something>.ts.net
```

```sh
sudo systemctl restart hydrosnooze
```

**Why the first line matters.** `tailscale serve` hands every request to the app from
the Pi's own address. Without this line, the app would take a request from the other
side of the world for one from the Pi itself. With it, anything that arrives from the
Pi without the scripts' key is treated as having come through Tailscale, and needs
signing in like everything else. The second line is the way back from signing in to
Withings, and the link on a notification.

- [ ] Both lines in, service restarted
- [ ] `journalctl -u hydrosnooze -n 20` shows it started without complaint

---

## Step 6. The phone

- [ ] Install **Tailscale** from the App Store and sign in with the same account. Allow
      it to add a VPN configuration when iOS asks
- [ ] In the Tailscale app's settings, turn on **VPN On Demand** if your version has
      it, so it reconnects by itself after the phone restarts. Otherwise, just leave
      Tailscale switched on; it uses very little battery when idle
- [ ] In Safari, open the address from step 4. Sign in. The password is already in
      Passwords from step 1
- [ ] **Share, Add to Home Screen.** This is the app's icon from now on, at home and
      away
- [ ] Keep the old `hydrosnooze.local` icon in a folder as a spare. It works on the home
      Wi-Fi with Tailscale off, which makes it the way in if Tailscale itself ever has a
      bad day

---

## Step 7. Prove it, rather than assume it

The same principle as the stress tests in `CHECKLIST.md`: "it worked when I set it up"
is not the same claim as "it works".

- [ ] **From outside.** Turn the phone's Wi-Fi off and open the new icon on mobile
      data. It loads, and the menu says **Signed in through Tailscale**
- [ ] **Closed to everything else.** Switch Tailscale off in its app and open the icon
      again. It cannot connect. That is the app not being on the internet
- [ ] **From a device that is not yours.** Try the address on a laptop or phone that is
      not signed in to Tailscale. It does not load
- [ ] **The notification** for the new sign-in arrived, saying "through Tailscale"
- [ ] **The power button from outside asks the plug first.** From outside the house one
      blind press on a unit that is already off would switch it on with nobody there, so
      the button sends the command that is checked against the plug instead. It takes
      up to two minutes to confirm, the same as the schedule's own switch-off
- [ ] **Signing out everywhere works.** Menu, **Every device**, then open the app again
      and it asks for the password. Sign back in

---

## Step 8. Withings, only if needed

Withings only needs signing in to again if it ever says to reconnect, and that can be
done at home from `http://hydrosnooze.local:8000` as before. To be able to do it from
outside too:

- [ ] In the Withings developer dashboard, add
      `https://hydrosnooze.<something>.ts.net/api/withings/callback` to the application's
      redirect URIs, next to the three already there

---

## Optional, and worth doing

**Approve new devices by hand.** In the admin console, under **Settings**, turn on
device approval. A device that signs in to the account then waits for approval before
it can reach anything.

**Only the app, nothing else.** By default every device on a tailnet can reach every
other device on every port. If Tailscale is only for this, the access controls can say
so: the phone may reach the app on the Pi and nothing more. In the admin console,
**Access controls**, replace the policy with this, putting the Pi's `100.x.y.z` address
from the Machines page in the first part:

```json
{
  "hosts": {
    "hydrosnooze": "100.x.y.z"
  },
  "acls": [
    { "action": "accept", "src": ["autogroup:member"], "dst": ["hydrosnooze:443"] }
  ]
}
```

This replaces the default rule that lets everything talk to everything, which is the
point. It also stops SSH to the Pi over Tailscale; SSH at home carries on as before.

**Security updates on the Pi.** It is reachable from more places now, so it is worth it
keeping itself patched:

```sh
sudo apt install unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

---

## When something goes wrong

| What | Why, and what to do |
|---|---|
| The app says HydroSnooze has no password yet | Step 1 was missed or `.env` lost the line. Run `./scripts/password.py --env /opt/hydrosnooze/.env` on the Pi and restart the service |
| It asks for the password on every page through the Tailscale address | The address is not https, so the phone will not keep the cookie. Use the `https://...ts.net` address from `tailscale serve status`, not the Pi's `100.x` address |
| "Too many wrong passwords" | Ten wrong in fifteen minutes pauses signing in for fifteen minutes, and sends a notification. The bed is unaffected. Wait it out |
| Forgotten the password | `./scripts/password.py` on the Pi, over SSH at home. It signs every device out |
| A phone goes missing | Two things, from any other device: **Every device** in the app's menu, and remove the phone on the Machines page in the Tailscale admin console. Then change the password with `./scripts/password.py` |
| The Tailscale address stopped working months later | Key expiry was left on for the Pi (step 3). Sign the Pi back in with `sudo tailscale up` and disable key expiry this time |
| `./scripts/diagnose.py` or `notify.py --test` get refused | The scripts' key is missing from `.env`. Running `./scripts/password.py` again writes one |
| Undo all of it | On the Pi: `sudo tailscale serve reset`, `sudo tailscale down`, and remove the two lines from step 5. The home Wi-Fi icon carries on exactly as before |

In every row the bed carries on running its nights. Nothing on this page is on the way
to the bed.
