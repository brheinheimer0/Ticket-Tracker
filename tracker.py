#!/usr/bin/env python3
"""
SeatGeek price tracker for one event.

Each run:
  1. Fetches the event's price stats from the SeatGeek Platform API.
  2. Appends a row to prices.csv (the price history).
  3. Decides whether to alert you (email, and optionally SMS) and records
     what it alerted on in state.json so you don't get spammed.

Config comes from environment variables (GitHub Actions secrets/vars):
  SEATGEEK_CLIENT_ID   required
  EVENT_ID             optional - the number at the end of the SeatGeek event URL.
                       If missing, the script searches for the game.
  TARGET_PRICE         default 60
  DROP_ALERT_PCT       default 8   (alert when price falls this % below the last alert)
  SMTP_HOST            default smtp.gmail.com
  SMTP_PORT            default 465
  SMTP_USER            your Gmail address
  SMTP_PASSWORD        a Gmail *app password* (not your normal password)
  ALERT_EMAIL_TO       where alerts go (defaults to SMTP_USER)
  TWILIO_SID / TWILIO_TOKEN / TWILIO_FROM / SMS_TO   optional, for text alerts

Usage:
  python tracker.py            # normal run
  python tracker.py --find     # list matching events so you can confirm the EVENT_ID
  python tracker.py --test-alert   # send a test email/SMS and exit
"""

import csv
import json
import os
import smtplib
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import requests

API = "https://api.seatgeek.com/2"
HERE = Path(__file__).resolve().parent
PRICES_CSV = HERE / "prices.csv"
STATE_JSON = HERE / "state.json"

# How we find the game if EVENT_ID isn't set.
SEARCH = {
    "q": "Arizona State Sun Devils Arizona Wildcats",
    "venue.city": "Tucson",
    "datetime_local.gte": "2026-11-28",
    "datetime_local.lte": "2026-11-29",
    "per_page": 25,
}

CSV_FIELDS = [
    "checked_at_utc", "event_id", "lowest_price", "lowest_sg_base_price",
    "median_price", "average_price", "highest_price", "listing_count", "ticket_count",
]


def env(name, default=None, required=False):
    val = os.environ.get(name, "").strip() or default
    if required and not val:
        sys.exit(f"Missing required setting: {name}")
    return val


def api_get(path, **params):
    params["client_id"] = env("SEATGEEK_CLIENT_ID", required=True)
    r = requests.get(f"{API}{path}", params=params, timeout=20)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- event lookup

def find_events():
    data = api_get("/events", **SEARCH)
    events = data.get("events", [])
    # Keep only events that mention both schools.
    def both(e):
        t = e.get("title", "").lower()
        return "arizona state" in t and "arizona" in t.replace("arizona state", "")
    return [e for e in events if both(e)] or events


def get_event():
    event_id = env("EVENT_ID")
    if event_id:
        return api_get(f"/events/{event_id}")
    matches = find_events()
    if not matches:
        sys.exit("Couldn't find the game. Run with --find, then set EVENT_ID.")
    return matches[0]


# ---------------------------------------------------------------- storage

def load_state():
    if STATE_JSON.exists():
        return json.loads(STATE_JSON.read_text())
    return {}


def save_state(state):
    STATE_JSON.write_text(json.dumps(state, indent=2) + "\n")


def append_row(row):
    new = not PRICES_CSV.exists()
    with PRICES_CSV.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


def history():
    if not PRICES_CSV.exists():
        return []
    with PRICES_CSV.open() as f:
        return [r for r in csv.DictReader(f) if r.get("lowest_price")]


# ---------------------------------------------------------------- alert logic

def decide_alerts(price, listings, state, rows):
    """Return a list of (key, message) alerts that should fire this run."""
    target = float(env("TARGET_PRICE", "60"))
    drop_pct = float(env("DROP_ALERT_PCT", "8"))
    alerts = []

    # 1. At or under your target. Fires once, then again only if it drops further.
    last_target_alert = state.get("last_target_alert_price")
    if price <= target and (last_target_alert is None or price < last_target_alert):
        alerts.append(("target", f"Lowest price is ${price:.0f}, at or under your ${target:.0f} target."))
        state["last_target_alert_price"] = price

    # 2. Meaningful drop since the last price we told you about.
    ref = state.get("last_notified_price")
    if ref and price <= ref * (1 - drop_pct / 100):
        alerts.append(("drop", f"Price dropped {100 * (ref - price) / ref:.0f}% (${ref:.0f} -> ${price:.0f})."))

    # 3. New all-time low for this game (only after we have some history).
    prior = [float(r["lowest_price"]) for r in rows]
    if len(prior) >= 6 and price < min(prior) and not any(k == "drop" for k, _ in alerts):
        alerts.append(("low", f"New lowest price since tracking began: ${price:.0f} (previous low ${min(prior):.0f})."))

    # 4. Supply warning: listings down 25%+ vs ~24h ago while price is up.
    if listings and len(rows) >= 48:
        day_ago = rows[-48]
        try:
            old_listings = int(day_ago["listing_count"])
            old_price = float(day_ago["lowest_price"])
            if old_listings and listings <= old_listings * 0.75 and price > old_price:
                if state.get("last_supply_warn_listings") != listings:
                    alerts.append(("supply", f"Inventory is shrinking: {old_listings} -> {listings} listings in ~24h "
                                             f"while the low went ${old_price:.0f} -> ${price:.0f}. Waiting is getting riskier."))
                    state["last_supply_warn_listings"] = listings
        except (ValueError, KeyError):
            pass

    if alerts:
        state["last_notified_price"] = price
    elif ref is None:
        state["last_notified_price"] = price  # baseline on first run
    return alerts


# ---------------------------------------------------------------- notifications

def send_email(subject, body):
    user = env("SMTP_USER")
    pw = env("SMTP_PASSWORD")
    if not (user and pw):
        print("Email not configured; skipping.")
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = env("ALERT_EMAIL_TO", user)
    msg.set_content(body)
    with smtplib.SMTP_SSL(env("SMTP_HOST", "smtp.gmail.com"), int(env("SMTP_PORT", "465"))) as s:
        s.login(user, pw)
        s.send_message(msg)
    print("Email sent.")


def send_sms(text):
    sid, token = env("TWILIO_SID"), env("TWILIO_TOKEN")
    frm, to = env("TWILIO_FROM"), env("SMS_TO")
    if not all([sid, token, frm, to]):
        return
    r = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        data={"From": frm, "To": to, "Body": text[:1500]},
        auth=(sid, token), timeout=20,
    )
    r.raise_for_status()
    print("SMS sent.")


def notify(event, price, alerts, stats):
    title = event.get("short_title") or event.get("title", "Your game")
    url = event.get("url", "")
    lines = [m for _, m in alerts]
    body = "\n".join([
        f"{title}",
        f"{event.get('datetime_local', '')} - {event.get('venue', {}).get('name', '')}",
        "",
        *lines,
        "",
        f"Lowest ${price:.0f} | median ${stats.get('median_price') or '?'} | "
        f"{stats.get('listing_count') or '?'} listings",
        url,
    ])
    send_email(f"Tickets ${price:.0f}: {title}", body)
    send_sms(f"{title}: lowest ${price:.0f}. {lines[0]} {url}")


# ---------------------------------------------------------------- main

def main():
    args = set(sys.argv[1:])

    if "--find" in args:
        for e in find_events():
            s = e.get("stats", {})
            print(f"{e['id']}  {e.get('datetime_local')}  {e.get('title')}  low=${s.get('lowest_price')}")
        return

    if "--test-alert" in args:
        send_email("SeatGeek tracker test", "If you got this, email alerts work.")
        send_sms("SeatGeek tracker test: SMS alerts work.")
        return

    event = get_event()
    start = event.get("datetime_utc")
    if start and datetime.fromisoformat(start).replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        print("Game has started; nothing to track.")
        return

    stats = event.get("stats", {}) or {}
    price = stats.get("lowest_price")
    if price is None:
        print("No listings/price right now.")
        return
    price = float(price)

    rows = history()
    append_row({
        "checked_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event_id": event["id"],
        **{k: stats.get(k) for k in CSV_FIELDS[2:]},
    })

    state = load_state()
    listings = stats.get("listing_count")
    alerts = decide_alerts(price, int(listings) if listings else None, state, rows)
    save_state(state)

    print(f"{event.get('title')}: low ${price:.0f}, {listings} listings. Alerts: {[k for k, _ in alerts] or 'none'}")
    if alerts:
        notify(event, price, alerts, stats)


if __name__ == "__main__":
    main()
