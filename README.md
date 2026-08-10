# Q1 Result Test Bot

A Telegram bot that reports Q1 (April-June quarter) results from NSE India.

## Commands

- `/q1` - Q1 financial results announced today on NSE
- `/upcoming` - Upcoming Q1 result board meetings (dates yet to be held)
- `/losers` - Q1-result companies trading down today (prices via Yahoo Finance)

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

1. Push this code to GitHub.
2. Sign up at https://app.koyeb.com and log in.
3. **Create App** -> **GitHub** and connect your repo (Koyeb auto-detects the
   `Dockerfile`).
4. Add the environment variable `TELEGRAM_BOT_TOKEN`.
5. Deploy. A health server on port `8000` keeps the service marked healthy.

The free instance (512MB RAM, 0.1 vCPU) runs the bot 24/7 with auto-restart on
crash.

> Important: only ONE instance may poll the bot at a time (Telegram returns
> `409 Conflict` otherwise). Once Koyeb is live, disable the GitHub Actions
> workflow in the repo's **Actions** tab.

### GitHub Actions (fallback, not continuous)

A workflow (`.github/workflows/bot.yml`) runs the bot in ~2h windows via cron.
It is free but has gaps between runs; use it only as a fallback.

### Render

1. Push the code to GitHub.
2. At https://render.com -> "New" -> "Background Worker" -> connect the repo
   (or use the `render.yaml` blueprint).
3. Add the `TELEGRAM_BOT_TOKEN` environment variable.
4. Free tier workers spin down after ~15 min of inactivity; **Starter+** keeps
   it running 24/7.

## Notes

- The bot polls the NSE corporate-announcements API. NSE requires a browser
  session cookie, which the bot establishes automatically.
- `/losers` prices come from Yahoo Finance; NSE blocks its own quote endpoints
  to automated clients.
- Results are informational; verify before trading.
