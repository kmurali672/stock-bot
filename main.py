import os, json, requests, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

BOT_TOKEN = os.environ.get("BOT_TOKEN")

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
ALERTS_FILE = os.path.join(DATA_DIR, "alerts.json")
STOCKS_FILE = os.path.join(DATA_DIR, "stocks.json")

USER_STATE = {}
SORT_MODE = {"mode": "default"}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0"})
CACHE = {}
CACHE_TTL = 60
MAX_WORKERS = 20

def load_alerts():
    try:
        if os.path.exists(ALERTS_FILE):
            with open(ALERTS_FILE, "r") as f:
                return json.load(f)
    except: pass
    return {}

def load_stocks():
    try:
        if os.path.exists(STOCKS_FILE):
            with open(STOCKS_FILE, "r") as f:
                data = json.load(f)
                if data:
                    print(f"Loaded {len(data)} stocks from {STOCKS_FILE}")
                    return set([s.upper() for s in data])
    except Exception as e:
        print(f"load_stocks error {e}")
    return set()

def save_alerts():
    try:
        with open(ALERTS_FILE, "w") as f:
            json.dump(ALERTS, f)
    except Exception as e:
        print(f"save_alerts error {e}")

def save_stocks():
    try:
        with open(STOCKS_FILE, "w") as f:
            json.dump(list(TRACKED_STOCKS), f)
        print(f"Saved {len(TRACKED_STOCKS)} stocks to {STOCKS_FILE}")
    except Exception as e:
        print(f"save_stocks error {e}")

ALERTS = load_alerts()
TRACKED_STOCKS = load_stocks()
MAIN_MENU = ReplyKeyboardMarkup([["📋 Watchlist"]], resize_keyboard=True)

def fetch_yahoo_raw(symbol):
    try:
        sym = f"{symbol.upper()}.NS"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
        r = SESSION.get(url, params={"range": "1d", "interval": "1m"}, timeout=12)
        data = r.json()
        chart = data.get("chart", {})
        if chart.get("error") is not None: return None
        result = chart.get("result")
        if not result: return None
        meta = result[0].get("meta", {})
        price = meta.get("regularMarketPrice")
        prev = meta.get("previousClose")
        if price is None or prev is None or price == 0: return None
        low = meta.get("regularMarketDayLow", price)
        high = meta.get("regularMarketDayHigh", price)
        pct = ((price - prev) / prev * 100) if prev else 0
        return price, pct, low, high
    except: return None

def get_full_data(symbol):
    su = symbol.upper()
    now = time.time()
    if su in CACHE and now - CACHE[su][0] < CACHE_TTL: return CACHE[su][1]
    vals = fetch_yahoo_raw(su)
    if vals is None: return 100.0, 0.0, 99.0, 101.0
    CACHE[su] = (now, vals)
    return vals

def is_valid_nse_symbol(symbol):
    try:
        vals = fetch_yahoo_raw(symbol)
        if vals is None: return False
        CACHE[symbol.upper()] = (time.time(), vals)
        return True
    except: return False

def get_multiple_data(symbols):
    results = {}; to_fetch = []; now = time.time()
    for s in symbols:
        su = s.upper()
        if su in CACHE and now - CACHE[su][0] < CACHE_TTL: results[su] = CACHE[su][1]
        else: to_fetch.append(su)
    if to_fetch:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            fut_map = {ex.submit(fetch_yahoo_raw, s): s for s in to_fetch}
            for fut in as_completed(fut_map):
                sym = fut_map[fut]
                try:
                    v = fut.result()
                    if v: CACHE[sym] = (time.time(), v); results[sym] = v
                    else: results[sym] = (100.0, 0.0, 99.0, 101.0)
                except: results[sym] = (100.0, 0.0, 99.0, 101.0)
    return [(s, *results.get(s.upper(), (100.0, 0.0, 99.0, 101.0))) for s in symbols]

async def show_watchlist_menu(update: Update):
    kb = [[InlineKeyboardButton("📈 My Positions", callback_data="menu_positions"), InlineKeyboardButton("⏰ Active Alerts", callback_data="menu_alerts")]]
    txt = "📋 *Watchlist Menu*"
    try:
        if update.callback_query: await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        else: await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    except: pass

async def show_positions(update: Update):
    try:
        if not TRACKED_STOCKS:
            txt = f"No stocks yet. Tap ➕ Add New Stock\nFile: {STOCKS_FILE}"
            kb = [[InlineKeyboardButton("➕ Add New Stock", callback_data="add_stock")], [InlineKeyboardButton("⬅️ Back", callback_data="back_watchlist")]]
            if update.callback_query: await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            else: await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            return
        fetched = get_multiple_data(list(TRACKED_STOCKS))
        stock_data = [(sym, p, pct, low, high) for sym, p, pct, low, high in fetched]
        if SORT_MODE["mode"] == "low_high": stock_data.sort(key=lambda x: x[2]); chg_label = "%CHG ↑"
        elif SORT_MODE["mode"] == "high_low": stock_data.sort(key=lambda x: x[2], reverse=True); chg_label = "%CHG ↓"
        else: stock_data.sort(key=lambda x: x[0]); chg_label = "%CHG ↕️"
        kb = [[InlineKeyboardButton("➕ Add New Stock", callback_data="add_stock")]]
        kb.append([InlineKeyboardButton("SYMBOL", callback_data="noop"), InlineKeyboardButton("LTP", callback_data="noop"), InlineKeyboardButton("TDL", callback_data="noop"), InlineKeyboardButton("TDH", callback_data="noop"), InlineKeyboardButton(chg_label, callback_data="sort_chg"), InlineKeyboardButton("1H", callback_data="noop"), InlineKeyboardButton("DEL", callback_data="noop")])
        for sym, p, pct, low, high in stock_data:
            url_1h = f"https://in.tradingview.com/chart/?symbol=NSE:{sym}&interval=60"
            kb.append([InlineKeyboardButton(f"🔵 {sym}", callback_data=f"stock_{sym}"), InlineKeyboardButton(f"{p:.1f}", callback_data=f"stock_{sym}"), InlineKeyboardButton(f"{low:.1f}", callback_data=f"stock_{sym}"), InlineKeyboardButton(f"{high:.1f}", callback_data=f"stock_{sym}"), InlineKeyboardButton(f"{pct:+.1f}%", callback_data=f"stock_{sym}"), InlineKeyboardButton("📈", url=url_1h), InlineKeyboardButton("🔴 X", callback_data=f"del_{sym}")])
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="back_watchlist")])
        txt = f"📈 *My Positions* ({len(stock_data)} stocks)"
        if update.callback_query:
            try: await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
            except: await update.callback_query.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        else: await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    except Exception as e:
        print(f"show_positions error {e}")

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
    fetched_map = {sym: (p, tdl, tdh) for sym, p, _, tdl, tdh in get_multiple_data(list(ALERTS.keys()))}
    lines = ["⏰ *Active Alerts*", "```", f"{'SYM':<8} {'TYPE':<5} {'SET':<7} {'LTP':<7}", "-" * 32]
    for sym, vals in ALERTS.items():
        p, _, _ = fetched_map.get(sym, (100.0, 99.0, 101.0))
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
    if d == "menu_positions": await show_positions(update); return
    if d == "menu_alerts": await show_active_alerts(update); return
    if d == "add_stock": USER_STATE[q.from_user.id] = {"action": "add_stock"}; await q.edit_message_text("➕ *Add New Stock*\nSend like: HDFC, TCS", parse_mode="Markdown"); return
    if d == "sort_chg":
        if SORT_MODE["mode"] == "default": SORT_MODE["mode"] = "low_high"
        elif SORT_MODE["mode"] == "low_high": SORT_MODE["mode"] = "high_low"
        else: SORT_MODE["mode"] = "default"
        await show_positions(update); return
    if d == "cancel_set": USER_STATE.pop(q.from_user.id, None); await show_positions(update); return
    if d.startswith("del_"): sym = d[4:]; TRACKED_STOCKS.discard(sym); ALERTS.pop(sym, None); save_alerts(); save_stocks(); CACHE.pop(sym, None); await show_positions(update); return
    if d.startswith("stock_"): await show_alert_options(update, d.split("_")[1]); return
    if d.startswith("setlow_") or d.startswith("sethigh_"):
        atype = "LOW" if "setlow" in d else "HIGH"; sym = d.split("_")[1]
        USER_STATE[q.from_user.id] = {"action": atype, "symbol": sym}
        prev_low = ALERTS.get(sym, {}).get("LOW"); prev_high = ALERTS.get(sym, {}).get("HIGH"); ltp = get_full_data(sym)[0]
        msg_lines = [f"✏️ Set new {atype}AlertPrice for {sym}", f"LTP: Rs.{ltp:.1f}", ""]
        if atype == "LOW":
            msg_lines.append(f"Prev Low: Rs.{prev_low:.1f}" if prev_low else "Prev Low: Not set")
            if prev_high: msg_lines.append(f"Prev High: Rs.{prev_high:.1f}")
        else:
            msg_lines.append(f"Prev High: Rs.{prev_high:.1f}" if prev_high else "Prev High: Not set")
            if prev_low: msg_lines.append(f"Prev Low: Rs.{prev_low:.1f}")
        msg_lines.append(""); msg_lines.append("Send price like: 85.5")
        kb = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_set")]]
        await q.edit_message_text("\n".join(msg_lines), reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"); return

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.message.from_user.id
        raw_text = update.message.text.strip()
        up_text = raw_text.upper()

        if up_text in ["/START", "START"]:
            USER_STATE.pop(uid, None)
            await start(update, context)
            return
        if "WATCHLIST" in up_text:
            USER_STATE.pop(uid, None)
            await show_watchlist_menu(update)
            return

        if uid in USER_STATE:
            state = USER_STATE[uid]
            if state["action"] == "add_stock":
                raw = raw_text.replace(';', ',')
                symbols = [s.strip().upper().replace(".NS", "") for s in raw.split(',') if s.strip()]
                added = []; invalid = []
                for sym in symbols:
                    if 1 <= len(sym) <= 15:
                        try:
                            if is_valid_nse_symbol(sym): TRACKED_STOCKS.add(sym); added.append(sym)
                            else: invalid.append(sym)
                        except: invalid.append(sym)
                    else: invalid.append(sym)
                if added: save_stocks()
                if invalid and not added:
                    USER_STATE[uid] = {"action": "add_stock"}
                else:
                    USER_STATE.pop(uid, None)
                await show_positions(update)
                if added: await update.message.reply_text(f"✅ {', '.join(added)} added!")
                if invalid: await update.message.reply_text(f"⚠️ No stock found: {', '.join(invalid)}\nSend correct again or /start to exit.")
                return
            elif raw_text.replace(".", "", 1).isdigit():
                sym = state["symbol"]; atype = state["action"]; price = float(raw_text)
                if sym not in ALERTS: ALERTS[sym] = {}
                ALERTS[sym][atype] = price; save_alerts(); USER_STATE.pop(uid, None)
                await update.message.reply_text(f"✅ {atype}AlertPrice {sym} = Rs.{price:.1f}"); await show_active_alerts(update); return

        raw = raw_text.replace(';', ',')
        symbols = [s.strip().upper().replace(".NS", "") for s in raw.split(',') if s.strip() and len(s.strip()) <= 15]
        if not symbols: return
        added = []; invalid = []
        for sym in symbols:
            try:
                if is_valid_nse_symbol(sym): TRACKED_STOCKS.add(sym); added.append(sym)
                else: invalid.append(sym)
            except: invalid.append(sym)
        if added: save_stocks()
        if added or invalid:
            await show_positions(update)
            if added: await update.message.reply_text(f"✅ {', '.join(added)} added!")
            if invalid: await update.message.reply_text(f"⚠️ No stock found: {', '.join(invalid)}")

    except Exception as e:
        print(f"menu crash fixed: {e}")
        try:
            USER_STATE.pop(update.message.from_user.id, None)
            await update.message.reply_text("Bot recovered. Send /start")
            await show_watchlist_menu(update)
        except: pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    USER_STATE.pop(update.message.from_user.id, None)
    await update.message.reply_text("Bot Ready! 📈", reply_markup=MAIN_MENU)
    await show_watchlist_menu(update)

def main():
    try: SESSION.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=True", timeout=10)
    except: pass
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(btn))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu))
    print(f"Bot Running - File Storage: {STOCKS_FILE} | {len(TRACKED_STOCKS)} tracked")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
