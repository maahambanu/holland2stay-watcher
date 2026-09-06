# Holland2Stay Rotterdam Studio Watcher

Checks Holland2Stay every ~5 minutes for new Rotterdam **Studio** listings
under a **"Stay"** contract (not Short-Stay), and pings you on **Gmail +
Telegram** the moment one appears — flagging whether it's Direct Booking
(act fast) or Lottery. No login needed to check — you log in and book
manually once you get the alert.

Runs entirely on GitHub Actions' free tier. No servers, no cost.

---

## 1. Before anything else: confirm the page structure

I couldn't load the real Holland2Stay listings page from where I built this
(no network access to that domain from this sandboxed environment), so
`parse_listings()` in `check_listings.py` is a best-effort guess. Do this
once, it takes 5 minutes:

1. Go to holland2stay.com, filter listings to Rotterdam (no login needed).
2. Open DevTools → **Network** tab, filter to **Fetch/XHR**, refresh the page.
3. Look for a request that returns **JSON** with the listings (a lot of
   sites like this load listings via an internal API, not raw HTML). If you
   find one:
   - Right-click it → **Copy → Copy as cURL**, and send me that — I'll
     rewrite `parse_listings()` to hit that JSON endpoint directly, which is
     far more reliable than HTML scraping.
4. If there's no such JSON call and it's server-rendered HTML instead,
   right-click a listing card → **Inspect**, and send me the HTML around
   one card — I'll fix the CSS selectors in `parse_listings()` to match.

Either way, everything else below (diffing, notifications, scheduling) is
ready to use as-is.

If plain requests do get a 403 from Cloudflare even on this public page,
say so and I'll swap in `cloudscraper` (TODO already flagged in the code).

### How "Stay" vs "Short-Stay" is detected

Short-Stay listings carry a pink "Short-stay" badge and a duration like
"6 months max". A regular "Stay" contract instead shows **"Indefinite"**
as its duration, with no Short-stay badge. `matches_filters()` uses that —
requiring "indefinite" in the card text and excluding anything badged
Short-stay — rather than just checking for the word "stay" (which also
appears inside "Short-stay").

## 2. Gmail: create an App Password

Regular Gmail passwords won't work for SMTP. Create an App Password:
1. Go to your Google Account → Security → 2-Step Verification (must be on).
2. Search "App passwords" → create one, name it e.g. `holland2stay-bot`.
3. Copy the 16-character password it gives you.

## 3. Telegram bot

1. In Telegram, message **@BotFather** → `/newbot` → follow the prompts →
   you'll get a bot token.
2. Start a chat with your new bot (search its username, send it any message).
3. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser
   — your `chat_id` is in the JSON response under `message.chat.id`.

## 4. Put it on GitHub

1. Create a new **private** repo on GitHub, push this folder to it.
2. Repo → Settings → Secrets and variables → Actions → **New repository
   secret**, add each of:
   - `GMAIL_USER` — your Gmail address
   - `GMAIL_APP_PASSWORD` — from step 3
   - `GMAIL_TO` — where to send alerts (can be same as GMAIL_USER)
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. Go to the **Actions** tab, enable workflows if prompted, and run
   "Check Holland2Stay Rotterdam studios" manually once (`Run workflow`
   button) to confirm it works before waiting for the schedule.

From then on it runs every 5 minutes automatically.

## Notes

- GitHub's cron scheduler is "at least every 5 minutes," but during high
  platform load runs can lag by several extra minutes. For most Lottery
  listings that's fine; for a fast-closing Direct Booking slot it's a real
  risk. If that turns out to matter in practice, the fix is moving the
  runner to something with tighter scheduling (Google Cloud Scheduler +
  Cloud Functions can reliably hit ~1 minute) — happy to build that version
  if 5-minute polling isn't fast enough in practice.
- Please keep this to a reasonable polling interval and personal use —
  it's your own account's view of the site, not a public scraper.
