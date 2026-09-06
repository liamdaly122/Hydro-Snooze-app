# Getting this running on the Mac

From nothing to a working app on the phone. About fifteen minutes, most of it waiting for downloads.

No experience assumed. If a line looks like nonsense, it is explained underneath.

## What you are actually doing

Three things have to be true before it runs:

1. The code is on the Mac
2. The Mac has Python and Node installed
3. One command gets run from inside the folder the code is in

That is the whole job. Everything below is one of those three.

---

## Step 1: get the code onto the Mac

The code lives on GitHub. GitHub is not a computer that runs anything, it is a filing cabinet. A copy
has to come down onto the Mac before anything can happen.

### The easy way: GitHub Desktop

1. Download it from **desktop.github.com** and drag it into Applications
2. Open it and sign in with your GitHub account
3. **File**, then **Clone Repository**
4. Choose `liamdaly122/Hydro-Snooze-app`
5. Look at where it says it will put it. The default is `~/Documents/GitHub/Hydro-Snooze-app`.
   Write that down, it is needed in step 4
6. Click **Clone**

That folder is now **the project folder**. Every instruction below happens inside it.

When there are updates later, open GitHub Desktop and click **Fetch origin**, then **Pull**. That is
the entire update process, and it is why this route is worth the download.

### Or, in Terminal

Only if git is already set up. GitHub will ask for a password here and then refuse to accept one,
which is exactly the sort of dead end the GitHub Desktop route avoids.

```sh
cd ~/Documents
git clone https://github.com/liamdaly122/Hydro-Snooze-app.git
```

There is only one branch in this repository, so this lands on the right code with nothing to switch.

---

## Step 2: check what the Mac already has

**Open Terminal.** Press **Cmd and Space** together, type `terminal`, press **Enter**.

A window of text appears. Whatever gets typed goes in after the `%` and runs when Enter is pressed.
Nothing here can break anything.

Type these two, pressing Enter after each:

```sh
python3 --version
node --version
```

| What comes back | What it means |
|---|---|
| `Python 3.11` or higher | Fine. Skip the Python download |
| `Python 3.9` or `3.10` | Needs the Python download. macOS ships an old one |
| `v18` or higher | Fine. Skip the Node download |
| `node: command not found` | Needs the Node download |

---

## Step 3: install whatever was missing

Both are ordinary Mac installers. Download, double click, click Continue until it finishes.

- **Python**: python.org/downloads, the big yellow button. Anything 3.11 or newer
- **Node**: nodejs.org, the **LTS** button

**Then quit Terminal completely with Cmd Q and open it again.** Terminal only notices newly installed
software when it starts up. Skipping this is the single most common reason the next step fails.

---

## Step 4: point Terminal at the project folder

Terminal is always "standing in" one folder. `cd` walks it to a different one. It stands for change
directory, and directory just means folder.

```sh
cd ~/Documents/GitHub/Hydro-Snooze-app
```

`~` means your home folder. Adjust that path if GitHub Desktop put it somewhere else.

**Shortcut if the path is awkward:** type `cd ` (including the space), then drag the folder from
Finder straight into the Terminal window. It fills in the path for you. Press Enter.

Check it worked:

```sh
ls
```

`ls` lists what is in the current folder. It should show `backend`, `frontend`, `scripts`,
`README.md` and a few others. If it does, Terminal is standing in the right place.

---

## Step 5: run it

```sh
./scripts/dev.sh
```

Read that out loud: **run** the file called `dev.sh`, which is inside the folder called `scripts`,
which is inside the folder I am currently standing in.

The `./` at the front means "starting from right here", as opposed to "somewhere on the system".

**That is all "one command in the project folder" ever meant.** The command itself is nothing
special. It just has to be typed while Terminal is standing in the right place, which is what step 4
was for.

The first run takes two or three minutes and prints a lot of text. It is installing things, once.
Every run after that takes about ten seconds.

When it is ready it prints:

```
  HydroSnooze is starting against a simulated unit.

  On this machine:  http://localhost:8000
  On your phone:    http://192.168.1.42:8000   (same Wi-Fi)
```

---

## Step 6: open it

**On the Mac**, open `http://localhost:8000` in Safari.

**On the phone**, on the same Wi-Fi, open the second address. That is the Mac's address on the home
network, so the number will be different from the example. Use the Share menu to add it to the Home
Screen and it opens like a real app.

The Mac has to stay awake, and that Terminal window has to keep running. That is precisely the job
the Raspberry Pi takes over later, and the only reason the Pi exists.

---

## Step 7: watch an evening happen

Tap the **spanner** tab at the bottom. Then **Jump to 21:29**, then **60x**. Wait about thirty
seconds.

The whole evening plays out: the unit switches on, forces Turbo, counts itself down to the phase 1
temperature, waits until 22:00, arms the schedule, then drops out of Turbo.

Switch back to the Terminal window. Every button press it would have sent over infrared is listed,
including the ones the unit would have swallowed.

---

## Stopping it

Click on the Terminal window and press **Ctrl and C** together. Control, not Command.

## Doing it again tomorrow

```sh
cd ~/Documents/GitHub/Hydro-Snooze-app
./scripts/dev.sh
```

Two lines, and the first is only needed because a fresh Terminal window always starts in your home
folder rather than where you left off.

---

## When something goes wrong

| It says | What to do |
|---|---|
| `no such file or directory` | Terminal is standing in the wrong folder. Run `ls`. If `scripts` is not in the list, go back to step 4 |
| `command not found: node` | Node is not installed, or Terminal was not quit and reopened after installing it |
| `Python 3.11 or newer is needed` | Install Python from python.org, then quit Terminal with Cmd Q and open it again |
| `address already in use` | It is already running in another Terminal window. Press Ctrl C in that one, or just close it |
| `permission denied` | Run `chmod +x scripts/dev.sh` once, then try again |
| The phone cannot load the page | Phone and Mac must be on the same Wi-Fi. A guest network will not work |
| Something else | Copy the last twenty lines of the Terminal output and send them to me |
