# Q1 Result Test Bot

A Telegram bot that reports Q1 (April-June quarter) results from NSE India.

## Commands

- `/q1` - Q1 financial results announced today on NSE
- `/upcoming` - Upcoming Q1 result board meetings (dates yet to be held)

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
connect a hosting service. Easy free options that auto-deploy from a GitHub
repo:

### Railway

1. Create a repo on GitHub and push this code.
2. Sign in at https://railway.app and click "New Project" -> "Deploy from GitHub repo".
3. Set the environment variable `TELEGRAM_BOT_TOKEN` in the project settings.
4. Railway auto-detects Python, installs `requirements.txt` and runs the bot.

### Render

1. Push the code to GitHub.
2. At https://render.com -> "New" -> "Web Service" -> connect the repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `python bot.py`
5. Add the `TELEGRAM_BOT_TOKEN` environment variable.
6. Free tier web services sleep after 15 min of inactivity. Add a free
   uptime monitor (e.g. https://uptimerobot.com) pinging your service URL to
   keep it awake.

### Fly.io

```bash
fly launch
fly secrets set TELEGRAM_BOT_TOKEN=your-token
fly deploy
```

## Notes

- The bot polls the NSE corporate-announcements API. NSE requires a browser
  session cookie, which the bot establishes automatically.
- Results are informational; verify before trading.
