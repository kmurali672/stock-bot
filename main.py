from flask import Flask
import threading
import os, json
import yfinance as yf
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

_flask_app = Flask(__name__)
@_flask_app.route('/')
def home(): return "Bot Running!"
def _run_web():
    _flask_app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
threading.Thread(target=_run_web, daemon=True).start()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
FILE = "stocks.json"
waiting = set()

def load_db():
    if not os.path.exists(FILE): return {}
    try:
        with open(FILE,'r') as f: return json.load(f)
    except: return {}

def save_db(d):
    with open(FILE,'w') as f: json.dump(f,d)

def get_data(sym):
    try:
        s = sym.strip().upper() # any case
        h = yf.Ticker(f"{s}.NS").history(period="2d")
        if h.empty: return None
        ltp = float(h['Close'].iloc[-1])
        prev = float(h['Close'].iloc[-2]) if len(h)>1 else ltp
        chg = ((ltp-prev)/prev*100) if prev else 0
        return {"sym": s, "ltp": ltp, "chg": chg}
    except:
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot Ready! 📈")
    kb = [[InlineKeyboardButton("📋 Watchlist Menu", callback_data="watchlist")]]
    await update.message.reply_text("📋 Watchlist Menu", reply_markup=InlineKeyboardMarkup(kb))

async def show_positions(obj, uid, edit=False):
    db = load_db()
    stocks = db.get(uid, [])
    if not stocks:
        txt = "No stocks. Tap ➕ Add New Stock"
        if edit: await obj.edit_message_text(txt)
        else: await obj.reply_text(txt)
        return
    rows = []
    for sym in stocks:
        d = get_data(sym)
        if d: rows.append(d)
    rows.sort(key=lambda x: x['chg'], reverse=True)
    btns = []
    for r in rows:
        dot = "🔵" if r['chg'] >= 0 else "🔴"
        btns.append([
            InlineKeyboardButton(f"{dot} {r['sym']} {r['ltp']:.2f} {r['chg']:+.2f}%", callback_data="noop"),
            InlineKeyboardButton("📈", callback_data=f"chart_{r['sym']}"),
            InlineKeyboardButton("❌", callback_data=f"del_{r['sym']}")
        ])
    btns.append([InlineKeyboardButton("⬅️ Back", callback_data="watchlist")])
    btns.append([InlineKeyboardButton("➕ Add New Stock", callback_data="add")])
    if edit:
        await obj.edit_message_text("My Positions:", reply_markup=InlineKeyboardMarkup(btns))
    else:
        await obj.reply_text("My Positions:", reply_markup=InlineKeyboardMarkup(btns))

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = str(update.effective_user.id)
    db = load_db()

    if text in ["My Positions", "📈 My Positions"]:
        await show_positions(update.message, uid, False)
        return
    if text in ["Active Alerts", "⏰ Active Alerts"]:
        await update.message.reply_text("⏰ Active Alerts - Coming soon")
        return
    if text == "➕ Add New Stock":
        waiting.add(uid)
        await update.message.reply_text("➕ Add New Stock\nSend like: HDFC, TCS\n(Any case: exideind, EXIDEIND)")
        return

    # FIX:, and ; support - comma suggested
    if uid in waiting or "," in text or ";" in text or text.replace(" ", "").replace(",", "").replace(";", "").isalnum():
        if uid in waiting: waiting.discard(uid)
        raw = text.replace(';', ',')
        symbols = [s.strip().upper() for s in raw.split(',') if s.strip()]
        if uid not in db: db[uid] = []
        added, failed = [], []
        for sym in symbols:
            d = get_data(sym)
            if d:
                if sym not in db[uid]:
                    db[uid].append(sym)
                    added.append(sym)
            else:
                failed.append(sym)
        save_db(db)
        rep = ""
        if added: rep += f"✅ Added: {', '.join(added)}\n"
        if failed: rep += f"❌ Failed: {', '.join(failed)}"
        await update.message.reply_text(rep or "❌ No valid")
        return

async def btn_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = str(q.from_user.id)
    data = q.data
    db = load_db()

    if data == "watchlist":
        # THIS WAS BROKEN - NOW FIXED - shows Active Alerts
        kb = [
            [InlineKeyboardButton("📈 My Positions", callback_data="mypos"),
             InlineKeyboardButton("⏰ Active Alerts", callback_data="alerts")],
            [InlineKeyboardButton("➕ Add New Stock", callback_data="add")]
        ]
        await q.edit_message_text("📋 Watchlist Menu", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "mypos":
        await show_positions(q, uid, True)

    elif data == "alerts":
        await q.message.reply_text("⏰ Active Alerts - No alerts set yet")

    elif data == "add":
        waiting.add(uid)
        await q.message.reply_text("➕ Add New Stock\nSend symbols like: HDFC, TCS or HDFC; TCS")

    elif data == "noop":
        return

    elif data.startswith("del_"):
        sym = data[4:]
        if uid in db and sym in db[uid]:
            db[uid].remove(sym)
            save_db(db)
            await q.message.reply_text(f"❌ Removed {sym}")

    elif data.startswith("chart_"):
        sym = data[6:]
        d = get_data(sym)
        if d:
            await q.message.reply_text(f"📈 {sym}: {d['ltp']:.2f} ({d['chg']:+.2f}%)")

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(btn_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
    print("OLD STYLE RESTORED with Active Alerts")
    app.run_polling()
