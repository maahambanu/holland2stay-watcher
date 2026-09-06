#!/usr/bin/env python3
"""
Holland2Stay Rotterdam Studio watcher.

Checks the Holland2Stay residences list for Rotterdam, filters to:
  - Property type: Studio
  - Contract: "Stay" (i.e. NOT "Short-Stay")
And notifies (Gmail + Telegram) on anything new, flagging whether it's
"Direct Booking" (act now!) or "Lottery" (weekly, less urgent).

IMPORTANT — READ THIS FIRST:
The listings page itself is public — no login needed to browse and see
booking status. You log in manually and book once you get the alert.

Cloudflare blocks plain HTTP requests even on this public page, so
fetching uses `curl_cffi` (impersonating a real Chrome browser's
TLS/HTTP fingerprint) instead of plain `requests`. If that ever starts
getting 403s too, the next step up is a real headless browser
(Playwright) or FlareSolverr.

The CSS selectors below are my best guess based on public info about
the site's structure — I could not load the real listings page from
here to confirm the exact markup (no network access to
holland2stay.com from this sandboxed environment). You will likely
need to adjust `parse_listings()` once, using your browser's DevTools
(see README "Finding the right selectors"). Everything else (diffing,
state, Gmail, Telegram, the GitHub Action) is ready to go as-is.
"""

import os
import re
import json
import sys
from pathlib import Path

import requests  # used for Gmail/Telegram calls, which don't hit Cloudflare
from curl_cffi import requests as cf_requests  # used only for fetching Holland2Stay
from bs4 import BeautifulSoup

STATE_PATH = Path(__file__).parent / "state" / "seen.json"

# The page that lists Rotterdam residences. Adjust if Holland2Stay's
# actual filter URL differs — check the address bar when you filter
# to Rotterdam on the site yourself.
LISTINGS_URL = "https://www.holland2stay.com/residences?city=rotterdam"

TARGET_CITY = "rotterdam"
TARGET_TYPE = "studio"
# A long-term "Stay" contract shows "Indefinite" as its duration and has no
# "Short-stay" badge; a Short-Stay listing shows a pink "Short-stay" badge
# and a duration like "6 months max". Checking for "indefinite" is a much
# more reliable signal than just looking for the word "stay" (which also
# appears inside "Short-stay").
REQUIRE_DURATION_TEXT = "indefinite"
EXCLUDE_BADGE_TEXT = "short-stay"

# Streets you especially care about — a match on any of these gets a
# "⭐ WATCHED STREET" flag in the notification so it stands out from
# other Rotterdam Studio Stay matches. Add more any time.
WATCHED_STREETS = ["galvanistraat"]


def get_session():
    """curl_cffi session impersonating a real Chrome browser's TLS/HTTP
    fingerprint — plain `requests` got a 403 from Cloudflare even on this
    public page, so we need something that looks more like an actual
    browser at the network level, not just via headers."""
    session = cf_requests.Session(impersonate="chrome124")
    session.headers.update({
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session


def fetch_html(session: requests.Session) -> str:
    resp = session.get(LISTINGS_URL, timeout=30)
    if resp.status_code == 403:
        raise RuntimeError(
            "Got a 403 even with curl_cffi's browser impersonation — "
            "Cloudflare may be issuing a JS challenge that needs an actual "
            "headless browser (Playwright) or FlareSolverr to solve, rather "
            "than a fingerprint-spoofing HTTP client. Let me know and I'll "
            "swap the fetch approach."
        )
    resp.raise_for_status()
    return resp.text


def parse_listings(html: str):
    """
    Returns a list of dicts:
      {id, name, url, city, property_type, contract, booking_mode, status}

    NOTE: selectors below are placeholders — inspect the real page (see
    README "Finding the right selectors") and adjust this function once.
    Many sites like this actually call a JSON API under the hood (check
    the Network tab for XHR/fetch calls returning JSON) — if you find
    one, swap this whole function for a simple `session.get(api_url).json()`
    and mapping instead; it'll be far more reliable than HTML scraping.
    """
    soup = BeautifulSoup(html, "html.parser")
    listings = []

    cards = soup.select("[class*='residence-card'], [class*='property-card'], article")
    for card in cards:
        text = card.get_text(" ", strip=True)
        link = card.select_one("a[href]")
        if not link:
            continue
        url = link.get("href", "")
        if url.startswith("/"):
            url = "https://www.holland2stay.com" + url

        name = link.get_text(strip=True) or text[:60]
        listing_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or url

        lower_text = text.lower()
        listings.append({
            "id": listing_id,
            "name": name,
            "url": url,
            "raw_text": lower_text,
        })

    return listings


def matches_filters(listing: dict) -> bool:
    text = listing["raw_text"]
    if TARGET_CITY not in text:
        return False
    if TARGET_TYPE not in text:
        return False
    # Exclude anything explicitly badged Short-stay
    if EXCLUDE_BADGE_TEXT in text or "short stay" in text:
        return False
    # Require the "Indefinite" duration that marks a long-term Stay contract
    if REQUIRE_DURATION_TEXT not in text:
        return False
    return True


def extract_details(listing: dict) -> dict:
    """Pull a few human-readable details out of the card's text for the
    notification message. Best-effort regex — fine if some come back empty,
    they're just extra context, not used for filtering."""
    text = listing["raw_text"]
    price = re.search(r"€\s?[\d.,]+", text)
    available = re.search(r"available per ([a-z]+ \d{1,2},?\s*\d{4})", text)
    size = re.search(r"(\d+(?:\.\d+)?)\s?m(?:2|²|\s|$)", text)

    return {
        "price": price.group(0) if price else "",
        "available_per": available.group(1).title() if available else "",
        "size_m2": size.group(1) if size else "",
    }


def booking_mode(listing: dict) -> str:
    text = listing["raw_text"]
    if "book direct" in text or "direct book" in text:
        return "DIRECT BOOKING"
    if "lottery" in text:
        return "Lottery"
    return "Unknown"


def is_watched_street(listing: dict) -> bool:
    text = listing["raw_text"]
    return any(street in text for street in WATCHED_STREETS)


def load_seen() -> set:
    if STATE_PATH.exists():
        return set(json.loads(STATE_PATH.read_text()))
    return set()


def save_seen(seen: set):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(sorted(seen), indent=2))


def send_gmail(new_listings):
    import smtplib
    from email.mime.text import MIMEText

    gmail_user = os.environ.get("GMAIL_USER")
    gmail_app_password = os.environ.get("GMAIL_APP_PASSWORD")
    gmail_to = os.environ.get("GMAIL_TO", gmail_user)

    if not gmail_user or not gmail_app_password:
        print("Gmail secrets not set, skipping email.")
        return

    lines = []
    for l in new_listings:
        star = "⭐ WATCHED STREET — " if l.get("watched") else ""
        extra = f" — {l['price']}" if l.get("price") else ""
        avail = f", available {l['available_per']}" if l.get("available_per") else ""
        lines.append(f"{star}{l['booking_mode']}: {l['name']}{extra}{avail}\n{l['url']}\n")
    body = "New Holland2Stay Rotterdam studio listing(s):\n\n" + "\n".join(lines)
    subject_star = "⭐ " if any(l.get("watched") for l in new_listings) else ""

    msg = MIMEText(body)
    msg["Subject"] = f"🏠 {subject_star}Holland2Stay: {len(new_listings)} new Rotterdam studio match(es)"
    msg["From"] = gmail_user
    msg["To"] = gmail_to

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_user, gmail_app_password)
        server.sendmail(gmail_user, [gmail_to], msg.as_string())
    print("Gmail sent.")


def send_telegram(new_listings):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram secrets not set, skipping push.")
        return

    for l in new_listings:
        star = "⭐ *WATCHED STREET*\n" if l.get("watched") else ""
        extra = f" — {l['price']}" if l.get("price") else ""
        avail = f"\nAvailable: {l['available_per']}" if l.get("available_per") else ""
        text = f"{star}🏠 *{l['booking_mode']}*\n{l['name']}{extra}{avail}\n{l['url']}"
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=15,
        )
    print("Telegram sent.")


def send_alert_text(message: str):
    """Used for operational alerts (e.g. cookie expired) via Telegram only."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": message},
        timeout=15,
    )


def main():
    session = get_session()

    try:
        html = fetch_html(session)
    except Exception as e:
        print(f"Fetch failed: {e}", file=sys.stderr)
        send_alert_text(f"⚠️ Holland2Stay checker error: {e}")
        sys.exit(1)

    all_listings = parse_listings(html)
    matching = [l for l in all_listings if matches_filters(l)]
    for l in matching:
        l["booking_mode"] = booking_mode(l)
        l.update(extract_details(l))
        l["watched"] = is_watched_street(l)

    seen = load_seen()
    new_listings = [l for l in matching if l["id"] not in seen]
    new_listings.sort(key=lambda l: not l["watched"])  # watched matches first

    print(f"Total cards parsed: {len(all_listings)}")
    print(f"Matching Rotterdam/Studio/Stay: {len(matching)}")
    print(f"New since last run: {len(new_listings)}")

    if new_listings:
        send_gmail(new_listings)
        send_telegram(new_listings)

    seen.update(l["id"] for l in matching)
    save_seen(seen)


if __name__ == "__main__":
    main()
