import os, json, requests, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

BOT_TOKEN = os.environ.get("BOT_TOKEN")

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
STOCKS_FILE = os.path.join(DATA_DIR, "stocks.json")
ALERTS_FILE = os.path.join(DATA_DIR, "alerts.json")

def load_stocks():
    try:
        if os.path.exists(STOCKS_FILE):
            with open(STOCKS_FILE, "r") as f:
                d = json.load(f)
                if d: return set([s.upper() for s in d])
    except: pass
    return set()

def load_alerts():
    try:
        if os.path.exists(ALERTS_FILE):
            with open(ALERTS_FILE, "r") as f:
                return json.load(f)
    except: pass
    return {}

TRACKED_STOCKS = load_stocks()
ALERTS = load_alerts()
USER_STATE = {}
SORT_MODE = {"mode": "default"}
CACHE = {}
CACHE_TTL = 60

def save_stocks():
    try:
        with open(STOCKS_FILE, "w") as f:
            json.dump(list(TRACKED_STOCKS), f)
    except: pass

def save_alerts():
    try:
        with open(ALERTS_FILE, "w") as f:
            json.dump(ALERTS, f)
    except: pass

MAIN_MENU = ReplyKeyboardMarkup([["📋 Watchlist"]], resize_keyboard=True)

def fetch_yahoo(symbol):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol.upper()}.NS"
        r = requests.get(url, params={"range":"1d","interval":"1m"}, headers={"User-Agent":"Mozilla/5.0"}, timeout=6)
        j = r.json()
        if j["chart"]["error"]: return None
        meta = j["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice")
        prev = meta.get("previousClose")
        if not price: return None
        low = meta.get("regularMarketDayLow", price)
        high = meta.get("regularMarketDayHigh", price)
        pct = (price - prev) / prev * 100 if prev else 0
        return price, pct, low, high
    except:
        return None

def get_many(symbols):
    res = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(fetch_yahoo, s): s for s in symbols}
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                v = fut.result(timeout=6)
                if v:
                    res[sym.upper()] = v
                    CACHE[sym.upper()] = (time.time(), v)
            except: pass
    out = []
    for s in symbols:
        su = s.upper()
        if su in res:
            p,pct,low,high = res[su]
            out.append((s,p,pct,low,high))
        elif su in CACHE:
            p,pct,low,high = CACHE[su][1]
            out.append((s,p,pct,low,high))
        else:
            out.append((s,0,0,0,0))
    return out

def get_full(symbol):
    su = symbol.upper()
    now = time.time()
    if su in CACHE and now - CACHE[su][0] < CACHE_TTL:
        return CACHE[su][1]
    v = fetch_yahoo(su)
    if not v: return (0,0,0,0)
    CACHE[su] = (now, v)
    return v

async def show_watchlist_menu(update):
    kb = [[InlineKeyboardButton("📈 My Positions", callback_data="menu_positions"), InlineKeyboardButton("⏰ Active Alerts", callback_data="menu_alerts")]]
    if update.callback_query:
        await update.callback_query.edit_message_text("📋 *Watchlist Menu*", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text("📋 *Watchlist Menu*", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def show_positions(update):
    if not TRACKED_STOCKS:
        kb = [[InlineKeyboardButton("➕ Add New Stock", callback_data="add_stock")],[InlineKeyboardButton("⬅️ Back", callback_data="back_watchlist")]]
        txt = "No stocks yet. Tap ➕ Add New Stock"
        if update.callback_query:
            await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))
        else:
            await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb))
        return

    data = get_many(list(TRACKED_STOCKS))
    if SORT_MODE["mode"] == "low_high":
        data.sort(key=lambda x: x[2]); label = "%CHG ↑"
    elif SORT_MODE["mode"] == "high_low":
        data.sort(key=lambda x: x[2], reverse=True); label = "%CHG ↓"
    else:
        data.sort(key=lambda x: x[0]); label = "%CHG ↕️"

    kb = [[InlineKeyboardButton("➕ Add New Stock", callback_data="add_stock")]]
    kb.append([InlineKeyboardButton("SYMBOL", callback_data="noop"), InlineKeyboardButton("LTP", callback_data="noop"), InlineKeyboardButton("TDL", callback_data="noop"), InlineKeyboardButton("TDH", callback_data="noop"), InlineKeyboardButton(label, callback_data="sort_chg"), InlineKeyboardButton("1H", callback_data="noop"), InlineKeyboardButton("DEL", callback_data="noop")])

    for sym,p,pct,low,high in data:
        url = f"https://in.tradingview.com/chart/?symbol=NSE:{sym}&interval=60"
        kb.append([
            InlineKeyboardButton(f"🔵 {sym}", callback_data=f"stock_{sym}"),
            InlineKeyboardButton(f"{p:.1f}", callback_data=f"stock_{sym}"),
            InlineKeyboardButton(f"{low:.1f}", callback_data=f"stock_{sym}"),
            InlineKeyboardButton(f"{high:.1f}", callback_data=f"stock_{sym}"),
            InlineKeyboardButton(f"{pct:+.1f}%", callback_data=f"stock_{sym}"),
            InlineKeyboardButton("📈", url=url),
            InlineKeyboardButton("🔴 X", callback_data=f"del_{sym}")
        ])
    kb.append([InlineKeyboardButton("🔄 Refresh", callback_data="refresh_prices"), InlineKeyboardButton("⬅️ Back", callback_data="back_watchlist")])

    txt = f"📈 *My Positions* ({len(data)} stocks)"
    if update.callback_query:
        await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def show_alert_options(update, symbol):
    p,pct,low,high = get_full(symbol)
    txt = f"*{symbol}* - LTP Rs.{p:.1f} ({pct:+.1f}%)\nTDL Rs.{low:.1f} | TDH Rs.{high:.1f}"
    u15 = f"https://in.tradingview.com/chart/?symbol=NSE:{symbol}&interval=15"
    u1h = f"https://in.tradingview.com/chart/?symbol=NSE:{symbol}&interval=60"
    u1d = f"https://in.tradingview.com/chart/?symbol=NSE:{symbol}&interval=D"
    kb = [[InlineKeyboardButton("🔻 SetLowAlertPrice", callback_data=f"setlow_{symbol}"), InlineKeyboardButton("🔺 SetHighAlertPrice", callback_data=f"sethigh_{symbol}")],[InlineKeyboardButton("📈 15M", url=u15), InlineKeyboardButton("📈 1H", url=u1h), InlineKeyboardButton("📈 Daily", url=u1d)],[InlineKeyboardButton("⬅️ Back", callback_data="menu_positions")]]
    await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def show_active_alerts(update):
    if not ALERTS:
        kb = [[InlineKeyboardButton("📈 My Positions", callback_data="menu_positions")]]
        txt = "⏰ No alerts"
        if update.callback_query:
            await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))
        else:
            await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb))
        return
    mp = {}
    for sym,p,_,_,_ in get_many(list(ALERTS.keys())): mp[sym] = p
    lines = ["⏰ *Active Alerts*","```",f"{'SYM':<8} {'TYPE':<5} {'SET':<7} {'LTP':<7}","-"*32]
    for sym, vals in ALERTS.items():
        p = mp.get(sym, 0)
        for atype, price in vals.items():
            lines.append(f"{sym:<8} {atype:<5} {price:<7.1f} {p:<7.1f}")
    lines.append("```")
    kb = [[InlineKeyboardButton("🔄 Refresh", callback_data="menu_alerts")],[InlineKeyboardButton("⬅️ Back", callback_data="back_watchlist")]]
    if update.callback_query:
        await update.callback_query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

async def btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    if d == "noop": return
    if d == "back_watchlist": await show_watchlist_menu(update); return
    if d in ["menu_positions","refresh_prices"]:
        if d == "refresh_prices": CACHE.clear()
        await show_positions(update); return
    if d == "menu_alerts": await show_active_alerts(update); return
    if d == "add_stock":
        USER_STATE[q.from_user.id] = {"action":"add_stock"}
        await q.edit_message_text("➕ *Add New Stock*\nSend like: HDFC, TCS", parse_mode="Markdown"); return
    if d == "sort_chg":
        if SORT_MODE["mode"] == "default": SORT_MODE["mode"] = "low_high"
        elif SORT_MODE["mode"] == "low_high": SORT_MODE["mode"] = "high_low"
        else: SORT_MODE["mode"] = "default"
        await show_positions(update); return
    if d == "cancel_set": USER_STATE.pop(q.from_user.id, None); await show_positions(update); return
    if d.startswith("del_"):
        sym = d[4:]; TRACKED_STOCKS.discard(sym); ALERTS.pop(sym, None); CACHE.pop(sym, None); save_stocks(); save_alerts()
        await show_positions(update); return
    if d.startswith("stock_"): await show_alert_options(update, d.split("_")[1]); return
    if d.startswith("setlow_") or d.startswith("sethigh_"):
        atype = "LOW" if "setlow" in d else "HIGH"; sym = d.split("_")[1]
        USER_STATE[q.from_user.id] = {"action": atype, "symbol": sym}
        prev = ALERTS.get(sym, {}).get(atype); ltp = get_full(sym)[0]
        line1 = f"Prev {atype}: Rs.{prev:.1f}" if prev else f"Prev {atype}: Not set"
        txt = f"✏️ Set new {atype}AlertPrice for {sym}\nLTP: Rs.{ltp:.1f}\n\n{line1}\n\nSend price like: 85.5"
        kb = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_set")]]
        await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb)); return

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = update.message.from_user.id
        raw = update.message.text.strip()
        up = raw.upper()
        if not raw: return
        if up in ["/START","START"]:
            USER_STATE.pop(uid, None); await start(update, context); return
        if "WATCHLIST" in up:
            USER_STATE.pop(uid, None); await show_watchlist_menu(update); return

        if uid in USER_STATE:
            st = USER_STATE[uid]
            if st["action"] == "add_stock":
                parts = [s.strip().upper().replace(".NS","") for s in raw.replace(","," ").replace(";"," ").split() if s.strip()]
                for s in parts:
                    if 1 <= len(s) <= 12: TRACKED_STOCKS.add(s)
                save_stocks(); USER_STATE.pop(uid, None)
                await show_positions(update)
                await update.message.reply_text(f"✅ {', '.join(parts)} added!")
                return
            if raw.replace(".","",1).isdigit():
                sym = st["symbol"]; atype = st["action"]; price = float(raw)
                if sym not in ALERTS: ALERTS[sym] = {}
                ALERTS[sym][atype] = price; save_alerts(); USER_STATE.pop(uid, None)
                await update.message.reply_text(f"✅ {atype} {sym} = Rs.{price:.1f}")
                await show_active_alerts(update); return

        parts = [s.strip().upper().replace(".NS","") for s in raw.replace(","," ").replace(";"," ").split() if s.strip()]
        if not parts: return
        for s in parts:
            if 1 <= len(s) <= 12: TRACKED_STOCKS.add(s)
        save_stocks(); await show_positions(update)
    except Exception as e:
        print(e)

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
    print(f"Bot Running Original | {len(TRACKED_STOCKS)} stocks")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
