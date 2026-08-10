# HSSV dog neuter appointment watcher

Checks the [Humane Society Silicon Valley Community Pet Clinic](https://www.hssv.org/spay-neuter-appointment/)
every 10 minutes for **dog neuter** surgery openings and emails when new dates appear.

At the time of writing, availability has been empty for months, so this waits for
slots to be released rather than polling something that is normally full.

## How it works

The booking page has no HTML form — it loads a Vetter Software widget that calls a
small JSON API. This repo calls the same public, read-only endpoints the widget uses:

| Endpoint | Purpose |
| --- | --- |
| `online-booking/reason` | appointment types (dog vs cat) |
| `online-booking/provider/{type_id}` | surgery types |
| `online-booking/timetable` | open slots per day |

Only the two **neuter** providers are watched (`Small Dog Neuter Surgery` and
`Large Dog Spay/Neuter Surgery`); the spay-only provider is ignored.

It does **not** book anything — it only reads availability and notifies.

## State

`state/seen_slots.json` records openings already reported, so a slot that stays open
does not re-alert every 10 minutes. If a date disappears and later returns, it alerts
again. `state/last_check.txt` is a daily heartbeat that keeps repository activity
fresh, since GitHub disables scheduled workflows after 60 days of inactivity.

## Setup

Requires three repository secrets:

| Secret | Value |
| --- | --- |
| `GMAIL_USER` | sending Gmail address |
| `GMAIL_APP_PASSWORD` | Google **app password** (needs 2FA enabled) |
| `MAIL_TO` | recipient address |

```bash
gh secret set GMAIL_APP_PASSWORD   # paste when prompted
```

An app password is required because Google blocks normal passwords over SMTP.
Create one at <https://myaccount.google.com/apppasswords>.

## Manual run

```bash
gh workflow run "HSSV dog neuter watch"
gh run watch
```

Send a test email to confirm SMTP works (does not check availability):

```bash
gh workflow run "HSSV dog neuter watch" -f test_email=true
```

Locally, without email:

```bash
python3 check.py
```

## Tuning

- Cadence: edit the `cron` line in `.github/workflows/watch.yml`
- Lookahead: set `HSSV_DAYS_AHEAD` (default 120 days)

## Caveats

- The API token is embedded in HSSV's public page. If they rotate it or change
  booking vendors, checks start failing — visible as failed runs, not as silence.
- Scheduled GitHub Actions runs can be delayed under load; `*/10` is best-effort.
