# ClearLedger desktop

A small desktop app for ClearLedger, made for a finance person rather than an engineer. It lets you run the engagement demo, trace a finance number back to the invoices and payments behind it, check what a change to a finance table would break and who must sign off, and see what this machine is missing, without a terminal.

The app is a shell. It does not contain ClearLedger and it does not bundle Python. It runs the ClearLedger core (the Python code in this repository) as child processes, each with a timeout, and shows what they print. The core has no part of the app in it, so it stays a command line tool that can run anywhere else too.

Every result says where it came from. Anything that did not run on your machine against your data is labelled RECORDED and is never shown as yours.

## Screens

- **Demo.** Runs `python -m engagement` (planted failures run now, then results recorded on the full system) and `python -m engagement walkthrough` (one finance question in four steps). Each walkthrough step keeps the label the core printed: LIVE (ran now against the real thing), STUB (ran now with a scripted stand-in for the language model), RECORDED (read from a file an earlier real run wrote) or SKIPPED (not run, with the reason). Without a Python 3.11 or newer, or without the ClearLedger folder, the screen shows the app's own recording instead, under a RECORDED banner, with the reason and the exact command to fix it.
- **Trace.** Pick a number (what a customer owes by how late, what a customer was billed and paid, or a won deal from order to cash), type a customer name or deal id, and the app runs `clearledger trace` on your database. It shows the records behind the number, each with its Odoo id and version, and whether they add up. With no database connected it offers the recorded Northwind Logistics trace from `docs/examples/`, labelled RECORDED.
- **Change check.** Pick a change (today: dropping the "more than 90 days late" column from the aging table). If ChangeLens and dbt are installed, the app runs `dbt parse`, `changelens sync` and `changelens preflight --format json --no-record` and shows BLOCK or not, the risk score, who must sign off and who to tell. Otherwise it shows the recorded example from `docs/examples/preflight-drop-column.txt`, labelled RECORDED.
- **Readiness.** A plain list of what the app found on this machine (the ClearLedger folder, Python and its version, the Postgres driver, dbt, ChangeLens, whether your database answers and has the finance tables, whether Odoo answers) and, for each thing missing, the exact command that fixes it.
- **Settings.** The ClearLedger folder, the Python to use, and the database address.

## Build from source

You need Node 22 or newer and npm. Nothing else is installed. Run these in this folder.

```
npm ci              # install the pinned build tools
npm test            # unit tests with Node's test runner (builds first)
npm run typecheck   # type-check the main process and the page
npm run smoke       # headless check, no window: demo, recorded trace, recorded change check, readiness
npm run check-dist  # dry check of the dmg configuration, builds no dmg
npm run dist        # unsigned arm64 dmg in dist/
```

`npm start` builds and opens the app. Run from source, the app uses this checkout as the ClearLedger folder.

`npm run smoke` opens no window and no database. It runs the demo for real when it finds a Python 3.11 or newer (otherwise it checks the recording), parses the recorded trace and change check, and builds the readiness list. Set `CLEARLEDGER_PYTHON=/usr/bin/python3` to see the fallback on a Mac whose only Python is the system 3.9.

## Settings

Each setting shows its value and where it came from. The environment wins over a saved setting.

- `CLEARLEDGER_HOME`: the ClearLedger folder (a checkout of this repository). Otherwise the saved choice, otherwise the checkout the app was built from.
- `CLEARLEDGER_PYTHON`: the Python to run the core with. Otherwise the saved choice, otherwise the folder's `.venv/bin/python`, otherwise `python3.13`, `python3.12`, `python3.11` or `python3` on PATH and in the usual install folders (apps opened from Finder do not get your shell PATH). Each is asked for its version; the first that is 3.11 or newer is used. One you name yourself is the only one tried.
- `CLEARLEDGER_DSN`: your warehouse database. Otherwise one you type in Settings.

Saved settings live in the app's user data folder (`settings.json`).

### The database address

The address may hold a password, so it is never written in plain text. When you type it in Settings it is held in memory for this session only, unless you tick "Remember it in the keychain": then it is encrypted with Electron's safeStorage, whose key is held in the macOS Keychain, and only the encrypted form is saved. If the keychain is not available, the box is off and the address stays in memory. Passwords are masked wherever the app shows the address. The address reaches the core only as `CLEARLEDGER_DSN` in the child's environment, never on its command line.

When you have not chosen a database, the app removes `CLEARLEDGER_DSN` from what it passes on. The walkthrough then uses the core's own default (the local Postgres socket in `/tmp`, database `warehouse`); if that does not answer, its first step is RECORDED, and if it does, the step is LIVE and says so.

## Where the recordings come from

- `recorded/demo.txt`, `recorded/walkthrough.txt` and `recorded/walkthrough.json`: `python -m engagement` and `python -m engagement walkthrough`, recorded on 2026-11-08 at commit f4ac081 with Python 3.11.18, with no database, no analyst environment, no dbt or ChangeLens, and `AGENT_HARBOR_URL` unset. `recorded/manifest.json` says the same, and the app shows it next to the recording.
- `docs/examples/trace-northwind.txt` and `docs/examples/preflight-drop-column.txt`: the repository's own recorded examples, copied into the packaged app.

All of it is the simulated company, never your data.

## Install the dmg

The dmg is not signed or notarised. Open it and drag ClearLedger to Applications. The first time, open System Settings, Privacy and Security, and choose Open Anyway. Or run this once:

```
xattr -dr com.apple.quarantine /Applications/ClearLedger.app
```

The packaged app has no ClearLedger folder of its own: choose your checkout in Settings, or set `CLEARLEDGER_HOME`.

## Security notes

- The page runs with context isolation on, Node integration off, the sandbox on and a content policy that allows no network and no inline script.
- The page can call a short list of named actions. It never passes a path to open or a command to run. Every action checks the caller is the app's own page.
- The core runs as a child process with an argument array, never through a shell. A customer name goes after `--`, so text starting with a dash is never read as an option.
- Files and folders are chosen only in a native dialog.
- Text from the core is put in the page as text, never as markup.
- The app makes no network calls of its own, except the readiness check, which opens and closes a connection to the Odoo address (`ODOO_URL`, default `http://localhost:8069`) to see whether it answers.

## App readiness

What stops a stranger today:

- ClearLedger is not bundled. You need a checkout of this repository and Python 3.11 or newer with the core's requirements (`python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt`). A Mac has only Python 3.9 out of the box, so a stranger installs Python first (python.org). Without it the Demo screen shows the recording.
- Trace needs a warehouse: Postgres 16 with this repository's dbt models built, which today means Odoo 17 installed from source, a simulated year loaded and `python -m clearledger.demo` run (README.md, "Run it"). There is no hosted or sample database, so without that work Trace shows only the recorded example.
- Change check needs dbt and ChangeLens, both from the requirements above. Without them it shows only the recorded example. The live check also writes ChangeLens's graph file (`.changelens/`, ignored by git) in the ClearLedger folder.
- The walkthrough's analyst step needs its own environment (`.venv-analyst`, see the CI workflow) and is SKIPPED without it.
- The app is unsigned and not notarised, and the dmg is arm64 only.
- There is no CI job for this app. Adding one means a change to `.github/workflows/`, which is the owner's call.
- No person has looked at the windows yet. The unit tests, the type-check and the headless smoke pass, but the screens, the native dialogs and the keychain prompt have not been checked by eye.
