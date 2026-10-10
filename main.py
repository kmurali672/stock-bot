from flask import Flask
import threading
import os, json
import yfinance as yf
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# --- Keep LIVE green on Render Free ---
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
        t = yf.Ticker(f"{s}.NS" if not s.endswith(".NS") else s)
        hist = t.history(period="5d")
        if hist.empty: return None
        ltp = float(hist['Close'].iloc[-1])
        prev = float(hist['Close'].iloc[-2]) if len(hist)>1 else ltp
        chg = ((ltp-prev)/prev*100) if prev else 0
        # For old style columns - using close values
        c1 = f"{ltp:.0f}" if ltp>100 else f"{ltp:.1f}"
        c2 = f"{prev:.0f}" if prev>100 else f"{prev:.1f}"
        c3 = f"{float(hist['High'].iloc[-1]):.0f}" if float(hist['High'].iloc[-1])>100 else f"{float(hist['High'].iloc[-1]):.1f}"
        return {"sym":s,"ltp":ltp,"chg":chg,"c1":c1,"c2":c2,"c3":c3}
    except: return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # OLD STYLE - exactly like your screenshot Sept 5
    await update.message.reply_text("Bot Ready! 📈")
    kb = [[InlineKeyboardButton("📋 Watchlist Menu", callback_data="watchlist")]]
    await update.message.reply_text("📋 Watchlist Menu", reply_markup=InlineKeyboardMarkup(kb))
    # Bottom reply keyboard like screenshot
    reply_kb = [["📈 My Positions"], ["⏰ Active Alerts"]]
    # keeping reply keyboard
    await update.message.reply_text("Menu:", reply_markup=ReplyKeyboardMarkup([["My Positions"],["Active Alerts"]], resize_keyboard=True))

async def show_positions(update_obj, uid, is_callback=False):
    db = load_db()
    stocks = db.get(uid, [])
    if not stocks:
        txt = "No stocks yet. Click ➕ Add New Stock"
        if is_callback: await update_obj.edit_message_text(txt)
        else: await update_obj.reply_text(txt)
        return
    rows=[]
    for sym in stocks:
        d=get_data(sym)
        if d: rows.append(d)
    rows.sort(key=lambda x:x['chg'], reverse=True)

    # Build OLD green style buttons - each row = dot + symbol + values + chart + X
    btns=[]
    for r in rows:
        dot = "🔵" if r['chg']>=0 else "🔴"
        chg_txt = f"{r['chg']:+.1f}%"
        # Old style: TCS 2304 2302 2364 -0.7% 📈 ❌
        label = f"{dot} {r['sym']} {r['c1']} {r['c2']} {r['c3']} {chg_txt}"
        btns.append([
            InlineKeyboardButton(label, callback_data=f"noop"),
            InlineKeyboardButton("📈", callback_data=f"chart_{r['sym']}"),
            InlineKeyboardButton("🔴 X", callback_data=f"del_{r['sym']}")
        ])
    btns.append([InlineKeyboardButton("⬅️ Back", callback_data="watchlist")])
    btns.append([InlineKeyboardButton("➕ Add New Stock", callback_data="add")])
    txt = "My Positions:"
    if is_callback: await update_obj.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(btns))
    else: await update_obj.reply_text(txt, reply_markup=InlineKeyboardMarkup(btns))

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = str(update.effective_user.id)

    if text in ["My Positions","📈 My Positions","/positions"]:
        await show_positions(update.message, uid, False)
        return
    if text in ["Active Alerts","⏰ Active Alerts"]:
        await update.message.reply_text("⏰ Active Alerts - Coming soon")
        return
    if text == "➕ Add New Stock":
        waiting.add(uid)
        await update.message.reply_text("➕ Add New Stock\nSend symbols like: HDFC, TCS\n(Any case works: exideind, EXIDEIND, ExideInd)")
        return

    # --- FIX: any case +, and ; support ---
    if uid in waiting or "," in text or ";" in text:
        if uid in waiting: waiting.discard(uid)
        raw = text.replace(';', ',')
        symbols = [s.strip().upper() for s in raw.split(',') if s.strip()]
        print(f"USER TYPED: {text} -> {symbols}")
        db = load_db()
        if uid not in db: db[uid]=[]
        added, failed = [], []
        for sym in symbols:
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
        await update.message.reply_text(reply or "❌ No valid symbols")
        await show_positions(update.message, uid, False)
        return
    # single stock case-insensitive
    if len(text)<=20 and text.replace(" ","").replace(".NS","").isalnum():
        uid_in = uid in waiting
        if uid_in or True:
            if uid_in: waiting.discard(uid)
            raw = text.replace(';', ',')
            symbols = [s.strip().upper() for s in raw.split(',') if s.strip()]
            if symbols:
                db = load_db()
                if uid not in db: db[uid]=[]
                added=[]
                for sym in symbols:
                    d=get_data(sym)
                    if d and sym not in db[uid]:
                        db[uid].append(sym)
                        added.append(sym)
                if added:
                    save_db(db)
                    await update.message.reply_text(f"✅ Added: {', '.join(added)}")
                    await show_positions(update.message, uid, False)
                    return

async def btn_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    await q.answer()
    uid=str(q.from_user.id)
    data=q.data
    db=load_db()

    if data=="watchlist":
        kb = [[InlineKeyboardButton("📈 My Positions", callback_data="mypos"),
               InlineKeyboardButton("⏰ Active Alerts", callback_data="alerts")],
              [InlineKeyboardButton("➕ Add New Stock", callback_data="add")]]
        await q.edit_message_text("📋 Watchlist Menu", reply_markup=InlineKeyboardMarkup(kb))
    elif data=="mypos":
        await show_positions(q, uid, True)
    elif data=="add":
        waiting.add(uid)
        await q.message.reply_text("➕ Add New Stock\nSend symbols like: HDFC, TCS or HDFC; TCS")
    elif data.startswith("del_"):
        sym=data[4:]
        if uid in db and sym in db[uid]:
            db[uid].remove(sym)
            save_db(db)
            await q.message.reply_text(f"❌ Removed {sym}")
            await show_positions(q.message, uid, False)
    elif data.startswith("chart_"):
        sym=data[6:]
        d=get_data(sym)
        if d: await q.message.reply_text(f"📈 {sym}: {d['ltp']:.2f} ({d['chg']:+.1f}%)")

if __name__=='__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(btn_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
    print("Bot Running - OLD STYLE RESTORED + AnyCase + ;, Support")
    app.run_polling()
