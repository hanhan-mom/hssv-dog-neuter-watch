# Dog neuter appointment watcher

Checks two Bay Area low-cost clinics every 10 minutes for **dog neuter** openings
and emails when new dates appear:

| Clinic | Booking vendor | Types watched |
| --- | --- | --- |
| [HSSV Community Pet Clinic](https://www.hssv.org/spay-neuter-appointment/) (San Jose) | Vetter Software | Small Dog Neuter, Large Dog Spay/Neuter |
| [Pets In Need](https://petsinneed.org/spayneuter/) (Palo Alto) | Acuity Scheduling | Medium Dog Neuter (21-50 lbs) |

Sized for a ~40 lb male dog. Pets In Need only books dogs up to 50 lbs online,
so it has no large-dog option despite what its page implies.

At the time of writing, availability has been empty for months, so this waits for
slots to be released rather than polling something that is normally full.

## How it works

Neither clinic exposes a plain HTML form; both load a booking widget that calls a
JSON API. This repo calls the same public, read-only endpoints those widgets use.

**HSSV** (Vetter), authenticated by a token published in the page itself:

| Endpoint | Purpose |
| --- | --- |
| `online-booking/reason` | appointment types (dog vs cat) |
| `online-booking/provider/{type_id}` | surgery types |
| `online-booking/timetable` | open slots per day |

**Pets In Need** (Acuity), authenticated by the page's `ownerKey`:

| Endpoint | Purpose |
| --- | --- |
| `/api/scheduling/v1/availability/month` | which dates are open (`month` must be `YYYY-MM-01`, current month or later) |
| `/api/scheduling/v1/availability/times` | slot times for a date (`startDate`/`endDate`) |

Spay-only types are ignored. It does **not** book anything — it only reads
availability and notifies.

## Scheduling

GitHub throttles frequent cron schedules: a `*/10` cron was observed firing only
about once an hour. So the workflow is triggered **hourly** and then loops
internally for 50 minutes, checking every 10 minutes (`LOOP_MINUTES` /
`INTERVAL_SECONDS`). That yields a real 10-minute cadence from one scheduled run.

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
