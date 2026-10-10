from flask import Flask
import threading
import os, json
import yfinance as yf
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# --- Render FREE fix - keeps LIVE green (doesn't affect UI) ---
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
        s = sym.strip().upper()
        ticker = yf.Ticker(f"{s}.NS" if not s.endswith(".NS") else s)
        hist = ticker.history(period="2d")
        if hist.empty: return None
        ltp = float(hist['Close'].iloc[-1])
        prev = float(hist['Close'].iloc[-2]) if len(hist)>1 else ltp
        chg = ((ltp-prev)/prev*100) if prev else 0
        return {"sym":s,"ltp":ltp,"chg":chg}
    except: return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [["My Positions"],["➕ Add New Stock"]]
    await update.message.reply_text("Bot Ready! 📈", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = str(update.effective_user.id)
    db = load_db()

    if text == "My Positions":
        stocks = db.get(uid, [])
        if not stocks:
            await update.message.reply_text("No stocks. Click ➕ Add New Stock")
            return
        rows=[]
        for sym in stocks:
            d=get_data(sym)
            if d: rows.append(d)
        rows.sort(key=lambda x:x['chg'], reverse=True)
        msg = "My Positions:\n\n"
        for r in rows:
            dot = "🔵" if r['chg']>=0 else "🔴"
            msg += f"{dot} {r['sym']} {r['ltp']:.2f} {r['chg']:+.2f}%\n"
        btns = [[InlineKeyboardButton(f"📈 {r['sym']}", callback_data=f"chart_{r['sym']}"), InlineKeyboardButton("❌", callback_data=f"del_{r['sym']}")] for r in rows]
        btns.append([InlineKeyboardButton("➕ Add New Stock", callback_data="add")])
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(btns))
        return

    if text == "➕ Add New Stock":
        waiting.add(uid)
        await update.message.reply_text("➕ Add New Stock\nSend symbols like: HDFC, TCS\n(Any case works: exideind, EXIDEIND, ExideInd)")
        return

    # --- FIX: case-insensitive +, and ; support - comma is suggested ---
    if uid in waiting or "," in text or ";" in text or (text.replace(" ","").replace(",","").replace(";","").isalnum() and len(text)<=50):
        if uid in waiting: waiting.discard(uid)
        raw = text.replace(';', ',') # accept both ; and,
        symbols = [s.strip().upper() for s in raw.split(',') if s.strip()]
        print(f"USER TYPED: {text} -> PARSED: {symbols}")

        if uid not in db: db[uid]=[]
        added, failed = [], []
        for sym in symbols:
            if not sym: continue
            d=get_data(sym)
            if d:
                if sym not in db[uid]:
                    db[uid].append(sym)
                    added.append(sym)
                else:
                    added.append(f"{sym} (already)")
            else:
                failed.append(sym)
        save_db(db)
        reply=""
        if added: reply+=f"✅ Added: {', '.join(added)}\n"
        if failed: reply+=f"❌ Failed: {', '.join(failed)}"
        if not reply: reply="❌ No valid symbols"
        await update.message.reply_text(reply)
        return

async def btn_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    await q.answer()
    uid=str(q.from_user.id)
    data=q.data
    db=load_db()
    if data=="add":
        waiting.add(uid)
        await q.message.reply_text("➕ Add New Stock\nSend symbols like: HDFC, TCS")
    elif data.startswith("del_"):
        sym=data[4:]
        if uid in db and sym in db[uid]:
            db[uid].remove(sym)
            save_db(db)
            await q.message.reply_text(f"❌ Removed {sym}")
    elif data.startswith("chart_"):
        sym=data[6:]
        d=get_data(sym)
        if d: await q.message.reply_text(f"📈 {sym}: {d['ltp']:.2f} ({d['chg']:+.2f}%)")

if __name__=='__main__':
    print("Bot Running - Old Style Same - Comma Suggested + ; Supported + AnyCase")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(btn_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
    app.run_polling()
