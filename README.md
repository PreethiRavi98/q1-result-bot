# Q1 Result Test Bot

A Telegram bot that reports Q1 (April-June quarter) results from NSE India.

## Commands

- `/q1` - Q1 financial results announced today on NSE
- `/upcoming` - Upcoming Q1 result board meetings (dates yet to be held)
- `/losers` - Q1-result companies trading down today (prices via Yahoo Finance)
- `/chatid` - show your chat ID to use with auto-notifications

## Auto-notifications (push on new results)

The bot continuously polls NSE (every `POLL_INTERVAL` seconds, default 1) and
pushes each new financial-result announcement to one chat.

- Set `NOTIFY_CHAT_ID` to your chat ID (get it with `/chatid`).
- `POLL_INTERVAL` controls the poll frequency (default `1` second).
- Already-seen announcements are tracked by NSE `seq_id`; a restart re-seeds
  today's list so old results aren't re-sent.

> Caution: NSE may rate-limit or block aggressive polling. 1s polling issues
> ~86k requests/day; keep `POLL_INTERVAL` reasonable (e.g. 30-60) unless
> necessary.

## Local setup

1. Get a bot token from [@BotFather](https://t.me/BotFather) on Telegram.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Set the token and run:

   ```bash
   export TELEGRAM_BOT_TOKEN="your-token"
   python bot.py
   ```

## Deploy to a 24/7 host

GitHub only hosts the code; to keep the bot running all day you must also
connect a hosting service. The recommended option below runs continuously on a
free instance.

### Koyeb (free, always-on)

[![Deploy to Koyeb](https://www.koyeb.com/static/images/deploy/button.svg)](https://app.koyeb.com/deploy?type=git&builder=dockerfile&repository=github.com/PreethiRavi98/q1-result-bot&branch=main&name=q1-result-bot&ports=8000;http;/)

1. Click the button above (or sign up at https://app.koyeb.com and create an
   app from the GitHub repo).
2. Select the **free** instance type.
3. Add the environment variable `TELEGRAM_BOT_TOKEN`.
4. Deploy. A health server on port `8000` keeps the service marked healthy.

The free instance (512MB RAM, 0.1 vCPU) restarts the bot on crash.

> Important: only ONE instance may poll the bot at a time (Telegram returns
> `409 Conflict` otherwise). Once Koyeb is live, disable the GitHub Actions
> workflow in the repo's **Actions** tab.
>
> Note: Koyeb free instances scale to zero after ~1h without incoming traffic.
> If the bot goes quiet, wake it by opening the app URL once.

### GitHub Actions (fallback, not continuous)

A workflow (`.github/workflows/bot.yml`) runs the bot in ~2h windows via cron.
It is free but has gaps between runs; use it only as a fallback.

### Render (free, web service)

1. At https://render.com -> "New" -> "Blueprint" -> connect the repo (or
   "Web Service"). The `render.yaml` blueprint defines the free web service.
2. Add the `TELEGRAM_BOT_TOKEN` environment variable.
3. (Optional) Add `NOTIFY_CHAT_ID` for auto-push of new results.
4. After deploy, Render gives the app a URL like `https://q1-result-bot.onrender.com`.

Render free web services spin down after 15 min without inbound traffic. The
included GitHub Actions workflow (`.github/workflows/bot.yml`) pings the app
every 10 min to keep it awake. Set the app URL as a repository **variable**
named `RENDER_APP_URL` (Settings -> Secrets and variables -> Actions -> Variables).

## Notes

- The bot polls the NSE corporate-announcements API. NSE requires a browser
  session cookie, which the bot establishes automatically.
- `/losers` prices come from Yahoo Finance; NSE blocks its own quote endpoints
  to automated clients.
- Results are informational; verify before trading.
