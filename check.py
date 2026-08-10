#!/usr/bin/env python3
"""Check HSSV Community Pet Clinic for dog neuter openings; email on new ones.

Designed to run as a one-shot GitHub Actions job. State lives in
state/seen_slots.json so repeat runs only alert on genuinely new openings.
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

BASE = "https://vettersoftware.com/barramundi/schedule/online-booking"
# Public widget token, published on the HSSV booking page itself.
TOKEN = ("gH5qDar5PnxHmff+fsqYB/09jVIaSz6u/zjcVQ+xILvvwnVcrfdmRzWmgmchhj1mf7wu"
         "hM76WYXAS1F/Bo7rgVbDom6QLTJC8pdLVtOO2lA=")
DOG_TYPE_ID = "fc7b3591-5ff3-4b"
NEUTER_PROVIDERS = {
    "5ff6d326-cb50-49": "Small Dog Neuter Surgery",
    "d930fa5d-e0a2-47": "Large Dog Spay/Neuter Surgery",
}
BOOKING_URL = "https://www.hssv.org/spay-neuter-appointment/"

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(HERE, "state")
SEEN_FILE = os.path.join(STATE_DIR, "seen_slots.json")
HEARTBEAT_FILE = os.path.join(STATE_DIR, "last_check.txt")

DAYS_AHEAD = int(os.environ.get("HSSV_DAYS_AHEAD", "120"))
CHUNK_DAYS = 30


def log(msg):
    print(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def get(path, params=None):
    url = f"{BASE}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Vetter-Token": TOKEN,
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    })
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, ValueError, OSError) as exc:
            last_err = exc
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"request failed after retries: {last_err}")


def scan():
    found = {}
    today = datetime.date.today()
    for pid, pname in NEUTER_PROVIDERS.items():
        for offset in range(0, DAYS_AHEAD, CHUNK_DAYS):
            start = today + datetime.timedelta(days=offset)
            end = start + datetime.timedelta(days=CHUNK_DAYS - 1)
            data = get("timetable", {
                "type_id": DOG_TYPE_ID,
                "provider_id": pid,
                "start": start.isoformat(),
                "end": end.isoformat(),
            })
            for day, slots in data["response"]["resources"].items():
                if slots:
                    found.setdefault(pname, {})[day[:10]] = slots
            time.sleep(1)
    return found


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

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=30) as server:
        server.login(user, password)
        server.send_message(msg)
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
    """Daily-granularity heartbeat.

    Keeps the repo active so GitHub doesn't auto-disable the schedule, without
    producing a commit on every single run.
    """
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


def main():
    if os.environ.get("TEST_EMAIL", "").lower() in ("1", "true", "yes"):
        log("test email mode")
        try:
            sent = send_email(
                "HSSV watcher test email",
                "This is a TEST from your HSSV dog neuter watcher.\n\n"
                "If you received this, email alerts are configured correctly.\n"
                "No appointment is actually available right now.\n\n"
                f"Booking page: {BOOKING_URL}\n",
            )
        except (smtplib.SMTPException, OSError) as exc:
            log(f"TEST EMAIL FAILED: {exc}")
            return 1
        return 0 if sent else 1

    try:
        found = scan()
    except RuntimeError as exc:
        log(f"CHECK FAILED: {exc}")
        return 1

    keys = {f"{p}|{d}" for p, days in found.items() for d in days}
    seen = load_seen()
    new = keys - seen

    touch_heartbeat()

    if new:
        lines = []
        for p, days in sorted(found.items()):
            for d in sorted(days):
                if f"{p}|{d}" in new:
                    times = ", ".join(
                        str(s.get("time", s))[:20] for s in days[d][:6]
                    ) if isinstance(days[d], list) else ""
                    lines.append(f"  * {d} - {p} ({len(days[d])} slot(s)) {times}")
        body = (
            "Dog neuter appointment openings just appeared at HSSV "
            "Community Pet Clinic:\n\n"
            + "\n".join(lines)
            + f"\n\nBook here: {BOOKING_URL}\n"
            + "\nNote: these go fast. The page uses a 'Request Appointment' "
            "widget.\n"
        )
        log("OPENINGS FOUND:\n" + "\n".join(lines))
        try:
            send_email("HSSV dog neuter appointment AVAILABLE", body)
        except (smtplib.SMTPException, OSError) as exc:
            log(f"EMAIL FAILED: {exc}")
            save_seen(seen | new)
            return 1
        save_seen(keys)
    else:
        if seen - keys:
            save_seen(keys)
        log(f"no new openings ({len(keys)} known open day(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
