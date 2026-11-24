# Territorial Cup ticket tracker

Checks SeatGeek every 30 minutes for **Arizona State at Arizona, Sat Nov 28, 2026, 7 p.m., Arizona Stadium** and emails you when it's a good time to buy.

## What triggers an alert

| Alert | When |
|---|---|
| **Target** | Lowest price is at or under `TARGET_PRICE` (default $60). Fires again only if it drops further. |
| **Drop** | Price fell `DROP_ALERT_PCT`% (default 8%) since the last price you were told about. |
| **New low** | Cheapest price seen since tracking started (after the first ~3 hours of history). |
| **Supply warning** | Listings down 25%+ over ~24h while price went up, so waiting is getting riskier. |

Every check is logged to `prices.csv`, so you build a real price history.

## Setup (about 15 minutes)

1. **SeatGeek key:** sign in at https://seatgeek.com/account/develop, create an app, and copy the **Client ID**.
2. **Gmail app password:** go to Google Account → Security → 2-Step Verification (it must be on) → **App passwords**. Create one called "ticket bot" and copy the 16-character password.
3. **GitHub repo:** create a new **private** repo and upload everything in this folder, including the `.github/workflows/track.yml` file.
4. **Secrets:** in the repo, open **Settings → Secrets and variables → Actions**.
   - Under **Secrets**, add:
     - `SEATGEEK_CLIENT_ID`
     - `SMTP_USER`: your Gmail address
     - `SMTP_PASSWORD`: the app password
     - `ALERT_EMAIL_TO`: optional, a different address to send alerts to
   - Under **Variables**, add:
     - `TARGET_PRICE` = `60`
     - `EVENT_ID`: the number at the end of the game's SeatGeek URL (`seatgeek.com/...-tickets/college-football/2026-11-28-7-pm/event/`**`12345678`**). This is optional, since the script searches for the game if it's missing, but setting it is more reliable.
5. **Test it:** go to the **Actions** tab → "Track ticket prices" → **Run workflow**. Check the log: you should see a line like `low $XX, NNN listings`, and a `prices.csv` commit should appear in the repo.

The workflow then runs by itself every 30 minutes and stops doing anything once kickoff passes. Disable it afterward in **Actions → ⋯ → Disable workflow**.

### Optional: text alerts

Many carriers no longer forward email to text, so SMS goes through Twilio. Create a Twilio account and get a phone number, then add these secrets: `TWILIO_SID`, `TWILIO_TOKEN`, `TWILIO_FROM` (your Twilio number, like +15205551234) and `SMS_TO` (your cell number). A trial account can only text verified numbers, and it adds a "Sent from trial" prefix.

A free alternative is to have the Gmail app on your phone send notifications for emails from yourself with "Tickets $" in the subject line.

## Running locally

```bash
pip install -r requirements.txt
export SEATGEEK_CLIENT_ID=...
python tracker.py --find        # list matching events + IDs
python tracker.py --test-alert  # with SMTP_* set: sends a test email
python tracker.py               # one real check
```
