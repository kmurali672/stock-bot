from flask import Flask
import threading
import os, json
import yfinance as yf
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

_app = Flask(__name__)
@_app.route('/')
def home(): return "Bot Running"
threading.Thread(target=lambda: _app.run(host='0.0.0.0', port=int(os.environ.get("PORT",10000))), daemon=True).start()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
FILE="stocks.json"
waiting=set()
def load_db():
    if not os.path.exists(FILE): return {}
    try:
        with open(FILE,'r') as f: return json.load(f)
    except: return {}
def save_db(d):
    with open(FILE,'w') as f: json.dump(f,d)
def get_data(sym):
    try:
        s=sym.strip().upper()
        h=yf.Ticker(f"{s}.NS").history(period="5d")
        if h.empty: return None
        ltp=float(h['Close'].iloc[-1]); prev=float(h['Close'].iloc[-2]) if len(h)>1 else ltp
        chg=((ltp-prev)/prev*100) if prev else 0
        return {"sym":s,"ltp":ltp,"chg":chg}
    except: return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot Ready! 📈")
    kb=[[InlineKeyboardButton("📋 Watchlist Menu", callback_data="watchlist")]]
    await update.message.reply_text("📋 Watchlist Menu", reply_markup=InlineKeyboardMarkup(kb))

async def show_pos(msg_obj, uid, edit=False):
    db=load_db(); stocks=db.get(uid,[])
    if not stocks:
        t="No stocks. Tap ➕ Add New Stock"
        if edit: await msg_obj.edit_message_text(t)
        else: await msg_obj.reply_text(t)
        return
    rows=[get_data(s) for s in stocks]; rows=[r for r in rows if r]
    rows.sort(key=lambda x:x['chg'], reverse=True)
    btns=[]
    for r in rows:
        dot="🔵" if r['chg']>=0 else "🔴"
        label=f"{dot} {r['sym']} {r['ltp']:.2f} {r['chg']:+.1f}%"
        btns.append([InlineKeyboardButton(label, callback_data="noop"),
                     InlineKeyboardButton("📈", callback_data=f"chart_{r['sym']}"),
                     InlineKeyboardButton("❌", callback_data=f"del_{r['sym']}")])
    btns.append([InlineKeyboardButton("⬅️ Back", callback_data="watchlist")])
    btns.append([InlineKeyboardButton("➕ Add New Stock", callback_data="add")])
    if edit: await msg_obj.edit_message_text("My Positions:", reply_markup=InlineKeyboardMarkup(btns))
    else: await msg_obj.reply_text("My Positions:", reply_markup=InlineKeyboardMarkup(btns))

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text=update.message.text.strip(); uid=str(update.effective_user.id)
    if text in ["My Positions","📈 My Positions"]:
        await show_pos(update.message, uid, False); return
    if text in ["Active Alerts","⏰ Active Alerts"]:
        await update.message.reply_text("⏰ Active Alerts - Coming soon"); return
    # Fix: any case +, and ; support, comma suggested
    if uid in waiting or "," in text or ";" in text or len(text)<=30:
        if uid in waiting: waiting.discard(uid)
        raw=text.replace(';', ',')
        syms=[s.strip().upper() for s in raw.split(',') if s.strip()]
        if not syms: return
        db=load_db()
        if uid not in db: db[uid]=[]
        added=[]; failed=[]
        for sym in syms:
            d=get_data(sym)
            if d:
                if sym not in db[uid]: db[uid].append(sym); added.append(sym)
                else: added.append(f"{sym} (already)")
            else: failed.append(sym)
        save_db(db)
        rep=""
        if added: rep+=f"✅ Added: {', '.join(added)}\n"
        if failed: rep+=f"❌ Failed: {', '.join(failed)}"
        await update.message.reply_text(rep or "❌ No valid")
        await show_pos(update.message, uid, False)

async def btn_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); uid=str(q.from_user.id); data=q.data; db=load_db()
    if data=="watchlist":
        kb=[[InlineKeyboardButton("📈 My Positions", callback_data="mypos"),
             InlineKeyboardButton("⏰ Active Alerts", callback_data="alerts")],
            [InlineKeyboardButton("➕ Add New Stock", callback_data="add")]]
        await q.edit_message_text("📋 Watchlist Menu", reply_markup=InlineKeyboardMarkup(kb))
    elif data=="mypos":
        await show_pos(q, uid, True)
    elif data=="alerts":
        await q.message.reply_text("⏰ Active Alerts - Coming soon")
    elif data=="add":
        waiting.add(uid)
        await q.message.reply_text("➕ Add New Stock\nSend like: HDFC, TCS (supports: exideind; suzlon, any case)")
    elif data.startswith("del_"):
        sym=data[4:]
        if uid in db and sym in db[uid]: db[uid].remove(sym); save_db(db)
        await q.message.reply_text(f"❌ Removed {sym}")
    elif data.startswith("chart_"):
        sym=data[6:]; d=get_data(sym)
        if d: await q.message.reply_text(f"📈 {sym}: {d['ltp']:.2f} ({d['chg']:+.1f}%)")

if __name__=='__main__':
    app=ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(btn_handler))
    app.add_handler(MessageHandler(filters.TEXT, handle_msg))
    print("OLD STYLE RESTORED")
    app.run_polling()
