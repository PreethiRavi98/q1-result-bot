import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, date
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

NSE_BASE = "https://www.nseindia.com"
NSE_ANNOUNCEMENTS = f"{NSE_BASE}/api/corporate-announcements"
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
NSE_DISCLAIMER = "\n_Source: NSE India. For informational purposes only._"

_session = None


def nse_session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(NSE_HEADERS)
        _session.get(NSE_BASE, timeout=15)
    return _session


_MONTHS = {name: i for i, name in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _parse_date(text):
    patterns = [
        (r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\s*,?\s*(\d{4})\b", "dmy"),
        (r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\b", "dm"),
        (r"\b([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s*(\d{4})\b", "mdy"),
    ]
    for pat, kind in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if not m:
            continue
        if kind == "mdy":
            mon, day, year = m.group(1), int(m.group(2)), m.group(3)
        else:
            day, mon = int(m.group(1)), m.group(2)
            year = m.group(3) if len(m.groups()) == 3 else None
        month = _MONTHS.get(mon[:3].lower())
        if not month:
            continue
        if not year:
            now = datetime.now()
            candidate = date(now.year, month, day)
            if candidate < now.date() - timedelta(days=120):
                candidate = date(now.year + 1, month, day)
            return candidate
        try:
            return date(int(year), month, day)
        except ValueError:
            continue
    return None


def fetch_announcements(from_date, to_date):
    params = {
        "index": "equities",
        "from_date": from_date.strftime("%d-%m-%Y"),
        "to_date": to_date.strftime("%d-%m-%Y"),
    }
    r = nse_session().get(NSE_ANNOUNCEMENTS, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def is_financial_result(item):
    text = item.get("attchmntText", "") or ""
    low = text.lower()
    result_terms = [
        "financial result",
        "unaudited financial",
        "audited financial",
        "quarter ended",
        "results for the quarter",
        "results of the company for the quarter",
        "standalone and consolidated financial",
    ]
    if not any(t in low for t in result_terms):
        return False
    exclude_terms = [
        "newspaper publication",
        "earnings call",
        "audio recording",
        "press release",
        "annual general meeting",
        "agm",
        "update on",
        "corrigendum",
        "revised",
        "scrutinizer",
    ]
    if any(t in low for t in exclude_terms):
        return False
    return True


def is_upcoming_result(item):
    text = item.get("attchmntText", "") or ""
    low = text.lower()
    if "result" not in low and "financial" not in low:
        return False
    if any(x in low for x in ["outcome", "held today", "held earlier",
                              "conference", "earnings call", "press release",
                              "newspaper publication", "corrigendum", "revised",
                              "webcast", "audio recording", "analyst meet",
                              "investor meet", "investors meet", "schedule of investor",
                              "annual general meeting", "agm", "shareholders meeting"]):
        return False
    if not any(x in low for x in ["board meeting", "intimation", "to be held",
                                  "scheduled", "will be held", "to consider and approve"]):
        return False
    meeting = _parse_date(text)
    if meeting is None or meeting < date.today():
        return False
    return True


MAX_ITEMS = 40
MAX_CHARS = 3800


def format_results(items, heading):
    lines = [heading]
    seen = set()
    for item in items:
        sym = item.get("symbol")
        if not sym or sym in seen:
            continue
        seen.add(sym)
        text = item.get("attchmntText", "")
        m = re.search(r"(?:approved|considered|submitted to the Exchange,? )?(.{0,140}?(?:Financial Results?|financial results?)[^.]*\.)", text, re.IGNORECASE)
        summary = m.group(1).strip() if m else text[:180]
        lines.append(f"\n*{sym}*")
        lines.append(summary)
        if len(seen) >= MAX_ITEMS or len("\n".join(lines)) > MAX_CHARS:
            lines.append(f"\n_... and {len(items) - len(seen)} more companies._")
            break
    return "\n".join(lines)


def format_upcoming(items, heading):
    lines = [heading]
    seen = set()
    for item in items:
        sym = item.get("symbol")
        if not sym or sym in seen:
            continue
        seen.add(sym)
        text = item.get("attchmntText", "")
        meeting = _parse_date(text)
        when = f" on {meeting:%d %b %Y}" if meeting else ""
        lines.append(f"\n*{sym}*{when}")
        lines.append(text[:180])
        if len(seen) >= MAX_ITEMS or len("\n".join(lines)) > MAX_CHARS:
            lines.append(f"\n_... and {len(items) - len(seen)} more companies._")
            break
    return "\n".join(lines)


LOSERS_COUNT = 15
YAHOO_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "1"))
NOTIFY_CHAT_ID = os.getenv("NOTIFY_CHAT_ID", "").strip()

WATCHLIST_FILE = os.getenv("WATCHLIST_FILE", "watchlist.json")
_watchlist_lock = threading.Lock()


def load_watchlist():
    try:
        with open(WATCHLIST_FILE) as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def save_watchlist(watchlist):
    with open(WATCHLIST_FILE, "w") as f:
        json.dump(sorted(watchlist), f)


_watchlist = load_watchlist()


def is_watched(symbol):
    with _watchlist_lock:
        return symbol in _watchlist


def fetch_yahoo_quote(symbol):
    r = requests.get(
        f"{YAHOO_BASE}/{symbol}.NS",
        params={"range": "1d", "interval": "1d", "includePrePost": "false"},
        headers=YAHOO_HEADERS,
        timeout=20,
    )
    r.raise_for_status()
    result = r.json().get("chart", {}).get("result")
    if not result:
        return None, None
    meta = result[0].get("meta", {})
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not price or not prev:
        return None, None
    return price, (price - prev) / prev * 100


async def cmd_losers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now().date()
    try:
        ann = fetch_announcements(today, today)
    except Exception as exc:
        await update.message.reply_text(f"NSE fetch failed: {exc}")
        return
    symbols = sorted({i.get("symbol") for i in ann if is_financial_result(i)})[:LOSERS_COUNT * 3]
    if not symbols:
        await update.message.reply_text("No Q1 result announcements found for today on NSE yet.")
        return
    with ThreadPoolExecutor(max_workers=8) as pool:
        quotes = dict(zip(symbols, pool.map(fetch_yahoo_quote, symbols)))
    rows = []
    for sym in symbols:
        price, pct = quotes[sym]
        if price is None or pct >= 0:
            continue
        rows.append((pct, sym, price))
    if not rows:
        await update.message.reply_text("No Q1-result companies are trading down today.")
        return
    rows.sort()
    lines = [f"*Q1-result losers ({today:%d %b %Y})*"]
    for pct, sym, price in rows[:LOSERS_COUNT]:
        lines.append(f"\n*{sym}*  Rs {price:,.2f}  ({pct:+.2f}%)")
    if len(rows) > LOSERS_COUNT:
        lines.append(f"\n_... and {len(rows) - LOSERS_COUNT} more companies._")
    await update.message.reply_text("\n".join(lines) + NSE_DISCLAIMER, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)


_seen_seq_ids = set()


def _announcement_key(item):
    return item.get("seq_id") or f"{item.get('symbol')}|{item.get('an_dt')}"


def _send_telegram(token, chat_id, text):
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown",
              "disable_web_page_preview": True},
        timeout=20,
    ).raise_for_status()


def monitor_loop(token):
    if not NOTIFY_CHAT_ID:
        print("NOTIFY_CHAT_ID not set; monitor disabled.")
        return
    print(f"Monitor running: poll every {POLL_INTERVAL}s, notify {NOTIFY_CHAT_ID}")
    while True:
        started = time.monotonic()
        try:
            today = datetime.now().date()
            ann = fetch_announcements(today, today)
            for item in ann:
                if not is_financial_result(item):
                    continue
                sym = item.get("symbol")
                if _watchlist and sym not in _watchlist:
                    continue
                key = _announcement_key(item)
                if key in _seen_seq_ids:
                    continue
                _seen_seq_ids.add(key)
                text = (item.get("attchmntText") or "")[:200]
                msg = f"*New Q1 result: {sym}*\n{text}{NSE_DISCLAIMER}"
                _send_telegram(token, NOTIFY_CHAT_ID, msg)
                print(f"Notified {sym} ({item.get('an_dt')})")
        except Exception as exc:
            print(f"Monitor poll error: {exc}")
        elapsed = time.monotonic() - started
        time.sleep(max(0.0, POLL_INTERVAL - elapsed))


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


def start_health_server():
    port = int(os.getenv("PORT", "8000"))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


async def cmd_q1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now().date()
    try:
        ann = fetch_announcements(today, today)
    except Exception as exc:
        await update.message.reply_text(f"NSE fetch failed: {exc}")
        return
    results = [i for i in ann if is_financial_result(i)]
    if not results:
        await update.message.reply_text("No Q1 result announcements found for today on NSE yet.")
        return
    msg = format_results(results, f"*Q1 Results announced today ({today:%d %b %Y})*")
    await update.message.reply_text(msg + NSE_DISCLAIMER, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)


async def cmd_upcoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now().date()
    try:
        ann = fetch_announcements(today - timedelta(days=15), today + timedelta(days=30))
    except Exception as exc:
        await update.message.reply_text(f"NSE fetch failed: {exc}")
        return
    results = [i for i in ann if is_upcoming_result(i)]
    if not results:
        await update.message.reply_text("No upcoming Q1 result board meetings found on NSE.")
        return
    results.sort(key=lambda i: (_parse_date(i.get("attchmntText", "")) or date.max))
    msg = format_upcoming(results, "*Upcoming Q1 Results (board meetings yet to be held)*")
    await update.message.reply_text(msg + NSE_DISCLAIMER, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)


async def cmd_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Your chat ID is `{update.effective_chat.id}`. "
        f"Set `NOTIFY_CHAT_ID` to it to receive auto-pushes.",
        parse_mode=ParseMode.MARKDOWN)


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /add SYMBOL, e.g. /add INFY")
        return
    sym = context.args[0].upper()
    if not re.fullmatch(r"[A-Z0-9&\-]{1,20}", sym):
        await update.message.reply_text(f"Invalid symbol: `{context.args[0]}`")
        return
    with _watchlist_lock:
        if sym in _watchlist:
            await update.message.reply_text(f"*{sym}* is already on your watchlist.")
            return
        _watchlist.add(sym)
        save_watchlist(_watchlist)
    await update.message.reply_text(
        f"Added *{sym}* to your watchlist. You'll be notified when it announces Q1 results.",
        parse_mode=ParseMode.MARKDOWN)


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /remove SYMBOL, e.g. /remove INFY")
        return
    sym = context.args[0].upper()
    with _watchlist_lock:
        if sym not in _watchlist:
            await update.message.reply_text(f"*{sym}* is not on your watchlist.")
            return
        _watchlist.discard(sym)
        save_watchlist(_watchlist)
    await update.message.reply_text(
        f"Removed *{sym}* from your watchlist.", parse_mode=ParseMode.MARKDOWN)


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with _watchlist_lock:
        symbols = sorted(_watchlist)
    if not symbols:
        await update.message.reply_text(
            "Your watchlist is empty. Add stocks with /add SYMBOL.")
        return
    await update.message.reply_text(
        "*Your watchlist*\n" + "\n".join(f"- {s}" for s in symbols),
        parse_mode=ParseMode.MARKDOWN)


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN environment variable not set")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text(
        "Hello! I report Q1 (April-June quarter) results from NSE.\n"
        "/q1 - today's Q1 result announcements\n"
        "/upcoming - upcoming Q1 result board meetings\n"
        "/losers - Q1-result companies trading down today\n"
        "/add SYMBOL - watch a stock for Q1 result alerts\n"
        "/remove SYMBOL - stop watching a stock\n"
        "/watchlist - show your watched stocks\n"
        "/chatid - show your chat ID for auto-notifications")))
    app.add_handler(CommandHandler("q1", cmd_q1))
    app.add_handler(CommandHandler("upcoming", cmd_upcoming))
    app.add_handler(CommandHandler("losers", cmd_losers))
    app.add_handler(CommandHandler("chatid", cmd_chatid))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    start_health_server()
    threading.Thread(target=monitor_loop, args=(token,), daemon=True).start()
    print("Bot started. Polling for updates...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
