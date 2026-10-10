import os, json, requests, time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

BOT_TOKEN = os.environ.get("BOT_TOKEN")
BACKUP_CHANNEL_ID = os.environ.get("BACKUP_CHANNEL_ID", "")

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
ALERTS_FILE = os.path.join(DATA_DIR, "alerts.json")
STOCKS_FILE = os.path.join(DATA_DIR, "stocks.json")

USER_STATE = {}
SORT_MODE = {"mode": "default"}
CACHE = {}
CACHE_TTL = 60
MAX_WORKERS = 10

# Ignore normal chat words - don't treat as stock
IGNORE_WORDS = {"perfect","ok","okay","thanks","thank you","hi","hello","good","great","done","nice"}

def backup_to_telegram():
    if not BACKUP_CHANNEL_ID or not BOT_TOKEN: return
    try:
        data = {"stocks": list(TRACKED_STOCKS), "alerts": ALERTS}
        text = f"BACKUP_JSON:{json.dumps(data)}"
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": BACKUP_CHANNEL_ID, "text": text}, timeout=8)
        if r.ok:
            msg_id = r.json()["result"]["message_id"]
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/pinChatMessage", json={"chat_id": BACKUP_CHANNEL_ID, "message_id": msg_id}, timeout=8)
            print(f"Backup pinned")
    except Exception as e:
        print(f"Backup fail {e}")

def backup_in_background():
    threading.Thread(target=backup_to_telegram, daemon=True).start()

def restore_from_telegram():
    if not BACKUP_CHANNEL_ID or not BOT_TOKEN: return None
    try:
        r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getChat", params={"chat_id": BACKUP_CHANNEL_ID}, timeout=8)
        if r.ok:
            pinned = r.json()["result"].get("pinned_message")
            if pinned and "text" in pinned and "BACKUP_JSON:" in pinned["text"]:
                j = pinned["text"].split("BACKUP_JSON:",1)[1]
                return json.loads(j)
    except Exception as e:
        print(f"Restore fail {e}")
    return None

def load_alerts():
    try:
        if os.path.exists(ALERTS_FILE):
            with open(ALERTS_FILE, "r") as f: return json.load(f)
    except: pass
    bk = restore_from_telegram()
    if bk and "alerts" in bk: return bk["alerts"]
    return {}

def load_stocks():
    try:
        if os.path.exists(STOCKS_FILE):
            with open(STOCKS_FILE, "r") as f:
                data = json.load(f)
                if data: return set([s.upper() for s in data])
    except: pass
    bk = restore_from_telegram()
    if bk and "stocks" in bk and bk["stocks"]:
        print(f"Restored {len(bk['stocks'])} from Telegram")
        return set([s.upper() for s in bk["stocks"]])
    return set()

def save_stocks_quick():
    try:
        with open(STOCKS_FILE, "w") as f: json.dump(list(TRACKED_STOCKS), f)
    except: pass

def save_alerts_quick():
    try:
        with open(ALERTS_FILE, "w") as f: json.dump(ALERTS, f)
    except: pass

def save_stocks():
    save_stocks_quick()
    backup_in_background()

def save_alerts():
    save_alerts_quick()
    backup_in_background()

ALERTS = load_alerts()
TRACKED_STOCKS = load_stocks()
MAIN_MENU = ReplyKeyboardMarkup([["📋 Watchlist"]], resize_keyboard=True)

def fetch_yahoo_raw(symbol):
    try:
        sym = f"{symbol.upper()}.NS"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
        r = requests.get(url, params={"range": "1d", "interval": "1m"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=4)
        data = r.json(); chart = data.get("chart", {})
        if chart.get("error") is not None: return None
        result = chart.get("result")
        if not result: return None
        meta = result[0].get("meta", {})
        price = meta.get("regularMarketPrice"); prev = meta.get("previousClose")
        if price is None or prev is None or price == 0: return None
        low = meta.get("regularMarketDayLow", price); high = meta.get("regularMarketDayHigh", price)
        pct = ((price - prev) / prev * 100) if prev else 0
        return price, pct, low, high
    except: return None

def get_full_data(symbol):
    su = symbol.upper(); now = time.time()
    if su in CACHE and now - CACHE[su][0] < CACHE_TTL: return CACHE[su][1]
    vals = fetch_yahoo_raw(su)
    if vals is None: return 100.0, 0.0, 99.0, 101.0
    CACHE[su] = (now, vals); return vals

def get_multiple_data_fast(symbols, use_cache_only=False):
    results = {}; to_fetch = []; now = time.time()
    for s in symbols:
        su = s.upper()
        if su in CACHE and now - CACHE[su][0] < 300:
            results[su] = CACHE[su][1]
        elif use_cache_only:
            results[su] = (0.0, 0.0, 0.0, 0.0)
        else:
            to_fetch.append(su)
    if use_cache_only:
        return [(s, *results.get(s.upper(), (0.0, 0.0, 0.0, 0.0))) for s in symbols]
    if to_fetch:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            fut_map = {ex.submit(fetch_yahoo_raw, s): s for s in to_fetch}
            for fut in as_completed(fut_map):
                sym = fut_map[fut]
                try:
                    v = fut.result(timeout=4)
                    if v: CACHE[sym] = (time.time(), v); results[sym] = v
                    else:
                        if sym not in results: results[sym] = (0.0, 0.0, 0.0, 0.0)
                except:
                    if sym not in results: results[sym] = (0.0, 0.0, 0.0, 0.0)
    return [(s, *results.get(s.upper(), (0.0, 0.0, 0.0, 0.0))) for s in symbols]

async def show_watchlist_menu(update: Update):
    kb = [[InlineKeyboardButton("📈 My Positions", callback_data="menu_positions"), InlineKeyboardButton("⏰ Active Alerts", callback_data="menu_alerts")]]
    txt = "📋 *Watchlist Menu*"
    if update.callback_query: await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else: await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def show_positions(update: Update):
    if not TRACKED_STOCKS:
        txt = "No stocks yet. Tap ➕ Add New Stock"
        kb = [[InlineKeyboardButton("➕ Add New Stock", callback_data="add_stock")], [InlineKeyboardButton("⬅️ Back", callback_data="back_watchlist")]]
        if update.callback_query: await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        else: await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return

    fetched = get_multiple_data_fast(list(TRACKED_STOCKS), use_cache_only=True)
    has_zero = any(p==0.0 for _, p, _, _, _ in fetched)
    stock_data = [(sym, p, pct, low, high) for sym, p, pct, low, high in fetched]
    if SORT_MODE["mode"] == "low_high": stock_data.sort(key=lambda x: x[2]); chg_label = "%CHG ↑"
    elif SORT_MODE["mode"] == "high_low": stock_data.sort(key=lambda x: x[2], reverse=True); chg_label = "%CHG ↓"
    else: stock_data.sort(key=lambda x: x[0]); chg_label = "%CHG ↕️"

    kb = [[InlineKeyboardButton("➕ Add New Stock", callback_data="add_stock")]]
    kb.append([InlineKeyboardButton("SYMBOL", callback_data="noop"), InlineKeyboardButton("LTP", callback_data="noop"), InlineKeyboardButton("TDL", callback_data="noop"), InlineKeyboardButton("TDH", callback_data="noop"), InlineKeyboardButton(chg_label, callback_data="sort_chg"), InlineKeyboardButton("1H", callback_data="noop"), InlineKeyboardButton("DEL", callback_data="noop")])
    for sym, p, pct, low, high in stock_data:
        url_1h = f"https://in.tradingview.com/chart/?symbol=NSE:{sym}&interval=60"
        ltp_txt = f"{p:.1f}" if p!=0 else "⌛"
        low_txt = f"{low:.1f}" if low!=0 else "-"
        high_txt = f"{high:.1f}" if high!=0 else "-"
        pct_txt = f"{pct:+.1f}%" if p!=0 else "-"
        kb.append([InlineKeyboardButton(f"🔵 {sym}", callback_data=f"stock_{sym}"), InlineKeyboardButton(ltp_txt, callback_data=f"stock_{sym}"), InlineKeyboardButton(low_txt, callback_data=f"stock_{sym}"), InlineKeyboardButton(high_txt, callback_data=f"stock_{sym}"), InlineKeyboardButton(pct_txt, callback_data=f"stock_{sym}"), InlineKeyboardButton("📈", url=url_1h), InlineKeyboardButton("🔴 X", callback_data=f"del_{sym}")])
    kb.append([InlineKeyboardButton("🔄 Refresh", callback_data="refresh_prices"), InlineKeyboardButton("⬅️ Back", callback_data="back_watchlist")])
    txt = f"📈 *My Positions* ({len(stock_data)} stocks)"

    if update.callback_query:
        try: await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        except: await update.callback_query.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    if has_zero:
        try:
            fetched_real = get_multiple_data_fast(list(TRACKED_STOCKS), use_cache_only=False)
            stock_data = [(sym, p, pct, low, high) for sym, p, pct, low, high in fetched_real]
            if SORT_MODE["mode"] == "low_high": stock_data.sort(key=lambda x: x[2])
            elif SORT_MODE["mode"] == "high_low": stock_data.sort(key=lambda x: x[2], reverse=True)
            else: stock_data.sort(key=lambda x: x[0])
            kb = [[InlineKeyboardButton("➕ Add New Stock", callback_data="add_stock")]]
            kb.append([InlineKeyboardButton("SYMBOL", callback_data="noop"), InlineKeyboardButton("LTP", callback_data="noop"), InlineKeyboardButton("TDL", callback_data="noop"), InlineKeyboardButton("TDH", callback_data="noop"), InlineKeyboardButton(chg_label, callback_data="sort_chg"), InlineKeyboardButton("1H", callback_data="noop"), InlineKeyboardButton("DEL", callback_data="noop")])
            for sym, p, pct, low, high in stock_data:
                url_1h = f"https://in.tradingview.com/chart/?symbol=NSE:{sym}&interval=60"
                kb.append([InlineKeyboardButton(f"🔵 {sym}", callback_data=f"stock_{sym}"), InlineKeyboardButton(f"{p:.1f}", callback_data=f"stock_{sym}"), InlineKeyboardButton(f"{low:.1f}", callback_data=f"stock_{sym}"), InlineKeyboardButton(f"{high:.1f}", callback_data=f"stock_{sym}"), InlineKeyboardButton(f"{pct:+.1f}%", callback_data=f"stock_{sym}"), InlineKeyboardButton("📈", url=url_1h), InlineKeyboardButton("🔴 X", callback_data=f"del_{sym}")])
            kb.append([InlineKeyboardButton("🔄 Refresh", callback_data="refresh_prices"), InlineKeyboardButton("⬅️ Back", callback_data="back_watchlist")])
            txt = f"📈 *My Positions* ({len(stock_data)} stocks)"
            if update.callback_query:
                try: await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
                except: pass
        except: pass

async def show_alert_options(update: Update, symbol):
    p, pct, low, high = get_full_data(symbol)
    txt = f"*{symbol}* - LTP Rs.{p:.1f} ({pct:+.1f}%)\nTDL Rs.{low:.1f} | TDH Rs.{high:.1f}"
    url_15m = f"https://in.tradingview.com/chart/?symbol=NSE:{symbol}&interval=15"; url_1h = f"https://in.tradingview.com/chart/?symbol=NSE:{symbol}&interval=60"; url_1d = f"https://in.tradingview.com/chart/?symbol=NSE:{symbol}&interval=D"
    kb = [[InlineKeyboardButton("🔻 SetLowAlertPrice", callback_data=f"setlow_{symbol}"), InlineKeyboardButton("🔺 SetHighAlertPrice", callback_data=f"sethigh_{symbol}")], [InlineKeyboardButton("📈 15M", url=url_15m), InlineKeyboardButton("📈 1H", url=url_1h), InlineKeyboardButton("📈 Daily", url=url_1d)], [InlineKeyboardButton("⬅️ Back", callback_data="menu_positions")]]
    await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def show_active_alerts(update: Update):
    if not ALERTS:
        txt = "⏰ No alerts"; kb = [[InlineKeyboardButton("📈 My Positions", callback_data="menu_positions")]]
        if update.callback_query: await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        else: await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return
    fetched_map = {sym: (p, tdl, tdh) for sym, p, _, tdl, tdh in get_multiple_data_fast(list(ALERTS.keys()), use_cache_only=True)}
    lines = ["⏰ *Active Alerts*", "```", f"{'SYM':<8} {'TYPE':<5} {'SET':<7} {'LTP':<7}", "-" * 32]
    for sym, vals in ALERTS.items():
        p, _, _ = fetched_map.get(sym, (0.0, 0.0, 0.0))
        for atype, set_price in vals.items(): lines.append(f"{sym:<8} {atype:<5} {set_price:<7.1f} {p:<7.1f}")
    lines.append("```"); full_text = "\n".join(lines)
    kb = [[InlineKeyboardButton("🔄 Refresh", callback_data="menu_alerts")], [InlineKeyboardButton("⬅️ Back", callback_data="back_watchlist")]]
    if update.callback_query:
        try: await update.callback_query.edit_message_text(full_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        except: await update.callback_query.message.reply_text(full_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else: await update.message.reply_text(full_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); d = q.data
    if d == "noop": return
    if d == "back_watchlist": await show_watchlist_menu(update); return
    if d == "menu_positions" or d == "refresh_prices":
        if d == "refresh_prices": CACHE.clear()
        await show_positions(update); return
    if d == "menu_alerts": await show_active_alerts(update); return
    if d == "add_stock": USER_STATE[q.from_user.id] = {"action": "add_stock"}; await q.edit_message_text("➕ *Add New Stock*\nSend like: HDFC, TCS", parse_mode="Markdown"); return
    if d == "sort_chg":
        if SORT_MODE["mode"] == "default": SORT_MODE["mode"] = "low_high"
        elif SORT_MODE["mode"] == "low_high": SORT_MODE["mode"] = "high_low"
        else: SORT_MODE["mode"] = "default"
        await show_positions(update); return
    if d == "cancel_set": USER_STATE.pop(q.from_user.id, None); await show_positions(update); return
    if d.startswith("del_"):
        sym = d[4:]
        TRACKED_STOCKS.discard(sym)
        ALERTS.pop(sym, None)
        save_stocks_quick()
        save_alerts_quick()
        CACHE.pop(sym, None)
        backup_in_background()
        await show_positions(update)
        return
    if d.startswith("stock_"): await show_alert_options(update, d.split("_")[1]); return
    if d.startswith("setlow_") or d.startswith("sethigh_"):
        atype = "LOW" if "setlow" in d else "HIGH"; sym = d.split("_")[1]
        USER_STATE[q.from_user.id] = {"action": atype, "symbol": sym}
        prev_low = ALERTS.get(sym, {}).get("LOW"); prev_high = ALERTS.get(sym, {}).get("HIGH"); ltp = get_full_data(sym)[0]
        msg_lines = [f"✏️ Set new {atype}AlertPrice for {sym}", f"LTP: Rs.{ltp:.1f}", ""]
        if atype == "LOW": msg_lines.append(f"Prev Low: Rs.{prev_low:.1f}" if prev_low else "Prev Low: Not set")
        else: msg_lines.append(f"Prev High: Rs.{prev_high:.1f}" if prev_high else "Prev High: Not set")
        msg_lines.append(""); msg_lines.append("Send price like: 85.5")
        kb = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_set")]]
        await q.edit_message_text("\n".join(msg_lines), reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"); return

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.message.from_user.id; raw_text = update.message.text.strip(); up_text = raw_text.upper()
        if not raw_text: return
        if raw_text.lower() in IGNORE_WORDS:
            return
        if up_text in ["/START", "START"]: USER_STATE.pop(uid, None); await start(update, context); return
        if "WATCHLIST" in up_text: USER_STATE.pop(uid, None); await show_watchlist_menu(update); return
        if uid in USER_STATE:
            state = USER_STATE[uid]
            if state["action"] == "add_stock":
                raw = raw_text.replace(';', ','); symbols = [s.strip().upper().replace(".NS","") for s in raw.split(',') if s.strip()]
                added = [s for s in symbols if 1 <= len(s) <= 15]
                for s in added: TRACKED_STOCKS.add(s)
                if added: save_stocks()
                USER_STATE.pop(uid, None)
                await show_positions(update)
                if added: await update.message.reply_text(f"✅ {', '.join(added)} added!")
                return
            elif raw_text.replace(".", "", 1).isdigit():
                sym = state["symbol"]; atype = state["action"]; price = float(raw_text)
                if sym not in ALERTS: ALERTS[sym] = {}
                ALERTS[sym][atype] = price; save_alerts(); USER_STATE.pop(uid, None)
                await update.message.reply_text(f"✅ {atype}AlertPrice {sym} = Rs.{price:.1f}"); await show_active_alerts(update); return
        raw = raw_text.replace(';', ','); symbols = [s.strip().upper().replace(".NS","") for s in raw.split(',') if s.strip()]
        if not symbols: return
        if len(symbols) == 1 and len(symbols[0]) > 15: return
        added = [s for s in symbols if 1 <= len(s) <= 15 and s not in IGNORE_WORDS]
        for s in added: TRACKED_STOCKS.add(s)
        if added: save_stocks()
        if added:
            await show_positions(update)
            await update.message.reply_text(f"✅ {', '.join(added)} added!")
    except Exception as e:
        print(f"menu error {e}")
        try: USER_STATE.pop(update.message.from_user.id, None); await update.message.reply_text("Recovered. Send /start")
        except: pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    USER_STATE.pop(update.message.from_user.id, None)
    await update.message.reply_text("Bot Ready! 📈", reply_markup=MAIN_MENU)
    await show_watchlist_menu(update)

def main():
    try: requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=True", timeout=5)
    except: pass
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(btn))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu))
    print(f"Bot FAST Delete Fixed | {len(TRACKED_STOCKS)} tracked | Backup: {BACKUP_CHANNEL_ID}")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
