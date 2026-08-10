import csv
import io
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
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


EQUITY_MASTER_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
_equity_map = {}
_equity_lock = threading.Lock()


def _normalise(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def load_equity_map():
    global _equity_map
    r = nse_session().get(EQUITY_MASTER_URL, timeout=60)
    r.raise_for_status()
    reader = csv.DictReader(io.StringIO(r.content.decode("utf-8-sig")))
    mapping = {"symbols": {}, "names": {}, "tokens": {}, "companies": []}
    for row in reader:
        sym = (row.get("SYMBOL") or "").strip().upper()
        name = (row.get("NAME OF COMPANY") or "").strip()
        if not sym or not name:
            continue
        norm_name = _normalise(name)
        tokens = re.findall(r"[a-z0-9]+", name.lower())
        mapping["symbols"].setdefault(_normalise(sym), sym)
        mapping["names"].setdefault(norm_name, sym)
        for tok in tokens:
            mapping["tokens"].setdefault(tok, []).append(sym)
        mapping["companies"].append((sym, name, norm_name, tokens))
    with _equity_lock:
        _equity_map = mapping
    return mapping


_IGNORED_WORDS = {"ltd", "limited", "pvt", "private", "plc", "co", "the"}


def resolve_symbol(query):
    q = _normalise(query)
    if not q:
        return None
    try:
        with _equity_lock:
            mapping = _equity_map
        if not mapping:
            mapping = load_equity_map()
    except Exception:
        return None
    with _equity_lock:
        syms = mapping["symbols"]
        names = mapping["names"]
        tokens = mapping["tokens"]
        companies = mapping["companies"]
        exact = syms.get(q)
    if exact:
        return exact
    q_tokens = [t for t in re.findall(r"[a-z0-9]+", query.lower()) if t not in _IGNORED_WORDS]
    best = None
    if q_tokens:
        for sym, name, norm_name, name_tokens in companies:
            cleaned = [t for t in name_tokens if t not in _IGNORED_WORDS]
            if not all(t in cleaned for t in q_tokens):
                continue
            score = sum(1 for t in q_tokens if t in cleaned and cleaned.count(t) >= 1)
            if best is None or score > best[0] or (score == best[0] and len(cleaned) < len(best[2])):
                best = (score, sym, cleaned)
    if best:
        return best[1]
    for norm_name, sym in names.items():
        if q in norm_name or norm_name in q:
            return sym
    return None


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


async def cmd_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Your chat ID is `{update.effective_chat.id}`. "
        f"Set `NOTIFY_CHAT_ID` to it to receive auto-pushes.",
        parse_mode=ParseMode.MARKDOWN)


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /add STOCK, e.g. /add Infosys or /add INFY")
        return
    query = " ".join(context.args)
    sym = resolve_symbol(query)
    if not sym:
        await update.message.reply_text(
            f"Could not find `{query}` on NSE. Try the exact symbol (e.g. `/add INFY`).",
            parse_mode=ParseMode.MARKDOWN)
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
        "/losers - Q1-result companies trading down today\n"
        "/add SYMBOL - watch a stock for Q1 result alerts\n"
        "/remove SYMBOL - stop watching a stock\n"
        "/watchlist - show your watched stocks\n"
        "/chatid - show your chat ID for auto-notifications")))
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
