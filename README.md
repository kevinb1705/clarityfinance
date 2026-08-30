# KeepClarity — Fortnightly Goals Ledger

Two parts, sharing the same logic:

1. **`ledger.html`** — the dashboard you open anytime. Edit balances there directly; it saves itself in your browser.
2. **`send_ledger_email.py`** — sends the same ledger as an email every second Thursday at 10pm, via GitHub Actions.

The two aren't linked automatically — the dashboard's storage is private to your browser. Keep **`config.json`** as the source of truth for the email, and update it whenever you update the dashboard — that's the one file you edit for the email side.

## How the money split works

Every fortnight, $1,000 is divided across 4 goals **by strict priority**:

| Priority | Goal | Share |
|---|---|---|
| 1 | India Transfer ($5,000 target) | 40% |
| 2 | Car ($6,000 target) | 30% |
| 3 | Emergency Fund (6 fortnights of income) | 20% |
| 4 | Investing (ongoing, no fixed target) | 10% |

When a higher-priority goal finishes, its share automatically cascades to whatever's still active, split proportionally by their existing weights. So if India Transfer completes, Car (priority 2) picks up the biggest chunk of the freed-up money, not an even split.

Completion dates shown are **real, pace-projected estimates** — calculated from the current balance and current fortnightly contribution, not arbitrary calendar dates. They'll shift automatically as your balances change.

Investing has no fixed target or deadline — it's meant to be an ongoing habit. Instead, you'll get an **INVEST NOW** alert (in both the dashboard and the email) every time that fortnight's contribution is about to push your Investing balance past a new $1,000 milestone.

## One-time setup (10 minutes)

1. **Create a new private GitHub repository** (e.g. `keepclarity`).

2. **Upload all 4 files from this folder to the repo root** — `ledger.html`, `config.json`, `send_ledger_email.py`, and the `.github` folder (which contains `.github/workflows/ledger-email.yml`). Make sure none of them end up nested inside an extra subfolder — they need to sit directly in the repo root, alongside each other.

3. **Create a Gmail App Password** (don't use your normal Gmail password):
   - Go to your Google Account → Security → 2-Step Verification (must be turned on) → App Passwords
   - Create one for "Mail", copy the 16-character code — you'll only see it once

4. **Add 3 repo secrets** — in your GitHub repo: Settings → Secrets and variables → Actions → New repository secret
   - `EMAIL_ADDRESS` — the Gmail address you'll send *from*
   - `EMAIL_APP_PASSWORD` — the app password from step 3
   - `RECIPIENT_EMAIL` — `kevin.bhambha@gmail.com` (config.json also has this as a fallback)

5. **Test it** — go to the "Actions" tab → "KeepClarity Fortnightly Ledger Email" → "Run workflow" → confirm. Wait ~30–60 seconds, check for a green checkmark, then check your Gmail inbox (and Spam, just in case).

That's it — from then on it runs itself every Thursday, and only actually sends on the real fortnightly week (checked against `next_payday` in `config.json`), landing at 10pm.

## Editing your data

Everything lives in **`config.json`**. Every payday:

- Update each goal's `"balance"`
- Update `"next_payday"` to the new date (this also drives which Thursdays it actually sends on — keep it accurate)
- If income changes, update `"income_per_fortnight"`

Edit it directly on GitHub (click the file → pencil icon → commit) — no code required. If the in-place pencil edit ever seems to silently not save (a known quirk on some mobile browsers), delete the file and use "Add file → Create new file" with the same filename instead — that method has proven more reliable.

## Daylight saving note

Sydney switches between AEST (UTC+10) and AEDT (UTC+11) twice a year. The schedule in `.github/workflows/ledger-email.yml` is set for AEST. During AEDT (roughly early October to early April), change the cron line from `"0 12 * * 4"` to `"0 11 * * 4"` so it still lands at 10pm local time.

## Adding, removing, or reordering goals

Edit the `"goals"` array in `config.json`. Each goal needs:
- `id`, `name`, `type` — `"lump"` (one-off target), `"buffer"` (target = income × `target_fortnights_of_income`), `"purchase"` (one-off target), or `"ongoing"` (no fixed target, just a recurring habit)
- `balance`, and `target` (or `target_fortnights_of_income` for `"buffer"` type — `"ongoing"` goals need neither)
- `priority` — lower number = higher priority
- `weight` — the fraction of the $1,000 this goal gets while active (all active goals' weights should sum to 1.0 for a clean split, though the system will still work and just redistribute proportionally if they don't)
- `desc` — a short description shown under the goal name
