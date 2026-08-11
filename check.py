#!/usr/bin/env python3
"""Watch Bay Area low-cost clinics for dog NEUTER openings and email on new ones.

Clinics covered:
  * HSSV Community Pet Clinic (San Jose)   - Vetter Software booking API
  * Pets In Need (Palo Alto)               - Acuity Scheduling API

Only neuter (male) appointment types are watched, sized for a 21-50 lb dog.
Availability is read-only; nothing is ever booked.
"""

import datetime
import json
import os
import smtplib
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

# --- HSSV (Vetter) ---------------------------------------------------------
HSSV_BASE = "https://vettersoftware.com/barramundi/schedule/online-booking"
# Public widget token, published on the HSSV booking page itself.
HSSV_TOKEN = ("gH5qDar5PnxHmff+fsqYB/09jVIaSz6u/zjcVQ+xILvvwnVcrfdmRzWmgmchhj1mf7wu"
              "hM76WYXAS1F/Bo7rgVbDom6QLTJC8pdLVtOO2lA=")
HSSV_DOG_TYPE_ID = "fc7b3591-5ff3-4b"
HSSV_PROVIDERS = {
    "5ff6d326-cb50-49": "Small Dog Neuter Surgery",
    "d930fa5d-e0a2-47": "Large Dog Spay/Neuter Surgery",
}
HSSV_URL = "https://www.hssv.org/spay-neuter-appointment/"

# --- Pets In Need (Acuity) -------------------------------------------------
PIN_BASE = "https://app.acuityscheduling.com/api/scheduling/v1/availability"
PIN_OWNER_KEY = "09903632"
PIN_CALENDAR_ID = "8049150"
PIN_TZ = "America/Los_Angeles"
# Neuter types sized for a 21-50 lb dog.
PIN_TYPES = {
    "54459873": "Medium Dog Neuter (Males) - 21-50 lbs",
}
PIN_URL = "https://petsinneed.org/spayneuter/"

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(HERE, "state")
SEEN_FILE = os.path.join(STATE_DIR, "seen_slots.json")
HEARTBEAT_FILE = os.path.join(STATE_DIR, "last_check.txt")

DAYS_AHEAD = int(os.environ.get("HSSV_DAYS_AHEAD", "120"))
CHUNK_DAYS = 30


def log(msg):
    print(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def fetch(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json",
                                               **(headers or {})})
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, ValueError, OSError) as exc:
            last_err = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"request failed after retries: {url[:80]}: {last_err}")


def scan_hssv():
    """Return {provider_name: {date: [slots]}} for HSSV."""
    found = {}
    today = datetime.date.today()
    for pid, pname in HSSV_PROVIDERS.items():
        for offset in range(0, DAYS_AHEAD, CHUNK_DAYS):
            start = today + datetime.timedelta(days=offset)
            end = start + datetime.timedelta(days=CHUNK_DAYS - 1)
            qs = urllib.parse.urlencode({
                "type_id": HSSV_DOG_TYPE_ID,
                "provider_id": pid,
                "start": start.isoformat(),
                "end": end.isoformat(),
            })
            data = fetch(f"{HSSV_BASE}/timetable?{qs}",
                         {"Vetter-Token": HSSV_TOKEN})
            for day, slots in data["response"]["resources"].items():
                if slots:
                    found.setdefault(pname, {})[day[:10]] = slots
            time.sleep(1)
    return found


def _months_ahead(n):
    """First-of-month dates covering the next n days, Acuity's required format."""
    today = datetime.date.today().replace(day=1)
    out = []
    for _ in range(max(1, n // 30 + 1)):
        out.append(today.isoformat())
        today = (today + datetime.timedelta(days=32)).replace(day=1)
    return out


def scan_pin():
    """Return {type_name: {date: [slots]}} for Pets In Need."""
    found = {}
    for tid, tname in PIN_TYPES.items():
        open_dates = []
        for month in _months_ahead(DAYS_AHEAD):
            qs = urllib.parse.urlencode({
                "owner": PIN_OWNER_KEY,
                "appointmentTypeId": tid,
                "calendarId": PIN_CALENDAR_ID,
                "timezone": PIN_TZ,
                "month": month,
            })
            data = fetch(f"{PIN_BASE}/month?{qs}")
            if isinstance(data, dict) and "status_code" not in data:
                open_dates += [d for d, avail in data.items() if avail]
            time.sleep(1)

        for date in sorted(set(open_dates)):
            qs = urllib.parse.urlencode({
                "owner": PIN_OWNER_KEY,
                "appointmentTypeId": tid,
                "calendarId": PIN_CALENDAR_ID,
                "timezone": PIN_TZ,
                "startDate": date,
                "endDate": date,
            })
            times = fetch(f"{PIN_BASE}/times?{qs}")
            slots = times.get(date, []) if isinstance(times, dict) else []
            found.setdefault(tname, {})[date] = slots or [{"time": date}]
            time.sleep(1)
    return found


def scan_all():
    """Return {clinic: {provider: {date: [slots]}}}, tolerating one clinic failing."""
    results, errors = {}, []
    for clinic, fn in (("HSSV", scan_hssv), ("Pets In Need", scan_pin)):
        try:
            results[clinic] = fn()
        except (RuntimeError, KeyError, TypeError) as exc:
            errors.append(f"{clinic}: {exc}")
            results[clinic] = {}
    return results, errors


def describe(slots):
    out = []
    for s in slots[:6]:
        if isinstance(s, dict):
            t = str(s.get("time", ""))
            n = s.get("slotsAvailable")
            out.append(f"{t[11:16] or t}" + (f" ({n} left)" if n else ""))
    return ", ".join(o for o in out if o)


def send_email(subject, body):
    user = os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    to_addr = os.environ.get("MAIL_TO") or user
    if not (user and password and to_addr):
        log("EMAIL SKIPPED: GMAIL_USER / GMAIL_APP_PASSWORD / MAIL_TO not all set")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465,
                          context=ssl.create_default_context(), timeout=30) as srv:
        srv.login(user, password)
        srv.send_message(msg)
    log(f"email sent to {to_addr}")
    return True


def load_seen():
    try:
        with open(SEEN_FILE) as fh:
            return set(json.load(fh))
    except (OSError, ValueError):
        return set()


def save_seen(keys):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(SEEN_FILE, "w") as fh:
        json.dump(sorted(keys), fh, indent=1)


def touch_heartbeat():
    os.makedirs(STATE_DIR, exist_ok=True)
    today = datetime.date.today().isoformat()
    try:
        with open(HEARTBEAT_FILE) as fh:
            if fh.read().strip() == today:
                return
    except OSError:
        pass
    with open(HEARTBEAT_FILE, "w") as fh:
        fh.write(today + "\n")


def check_once():
    """One pass. Returns exit code."""
    results, errors = scan_all()
    for err in errors:
        log(f"CHECK ERROR {err}")

    keys = {
        f"{clinic}|{prov}|{date}"
        for clinic, provs in results.items()
        for prov, days in provs.items()
        for date in days
    }
    seen = load_seen()
    new = keys - seen
    touch_heartbeat()

    if new:
        sections = []
        for clinic, provs in sorted(results.items()):
            lines = [
                f"  * {date} - {prov} [{len(days[date])} slot(s)] {describe(days[date])}"
                for prov, days in sorted(provs.items())
                for date in sorted(days)
                if f"{clinic}|{prov}|{date}" in new
            ]
            if lines:
                url = HSSV_URL if clinic == "HSSV" else PIN_URL
                sections.append(f"{clinic}\n" + "\n".join(lines) + f"\n  Book: {url}")
        body = ("New dog neuter openings:\n\n" + "\n\n".join(sections)
                + "\n\nThese go fast - book as soon as you can.\n")
        log("OPENINGS FOUND:\n" + "\n\n".join(sections))
        try:
            send_email("Dog neuter appointment AVAILABLE", body)
        except (smtplib.SMTPException, OSError) as exc:
            log(f"EMAIL FAILED: {exc}")
            save_seen(seen | new)
            return 1
        save_seen(keys)
    else:
        if seen - keys:
            save_seen(keys)
        log(f"no new openings ({len(keys)} known open day(s))")

    return 1 if errors else 0


def main():
    if os.environ.get("TEST_EMAIL", "").lower() in ("1", "true", "yes"):
        log("test email mode")
        try:
            ok = send_email(
                "Dog neuter watcher test email",
                "TEST from your dog neuter watcher.\n\n"
                "If you got this, email alerts work.\n"
                "Watching HSSV (San Jose) and Pets In Need (Palo Alto).\n",
            )
        except (smtplib.SMTPException, OSError) as exc:
            log(f"TEST EMAIL FAILED: {exc}")
            return 1
        return 0 if ok else 1

    loop_minutes = int(os.environ.get("LOOP_MINUTES", "0"))
    interval = int(os.environ.get("INTERVAL_SECONDS", "600"))
    if loop_minutes <= 0:
        return check_once()

    deadline = time.time() + loop_minutes * 60
    rc = 0
    while True:
        rc = check_once()
        if time.time() + interval >= deadline:
            break
        time.sleep(interval)
    return rc


if __name__ == "__main__":
    sys.exit(main())
