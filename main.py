from flask import Flask
import threading
import os, json, requests
import yfinance as yf
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# --- Keep Render Alive (Free Web Service Fix) ---
app = Flask(__name__)
@app.route('/')
def home():
    return "Bot is Running! 🤖"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_web, daemon=True).start()

# --- Config ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
STOCK_FILE = "stocks.json"
waiting_for_stock = set()

# --- Storage ---
def load_stocks():
    if not os.path.exists(STOCK_FILE):
        return {}
    try:
        with open(STOCK_FILE, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_stocks(data):
    with open(STOCK_FILE, 'w') as f:
        json.dump(data, f)

def get_stock_data(symbol):
    try:
        sym = symbol.strip().upper()
        if not sym.endswith(".NS"):
            ticker_sym = f"{sym}.NS"
        else:
            ticker_sym = sym
        
        ticker = yf.Ticker(ticker_sym)
        hist = ticker.history(period="2d")
        if hist.empty:
            return None, f"{sym} not found"
        ltp = float(hist['Close'].iloc[-1])
        prev = float(hist['Close'].iloc[-2]) if len(hist) > 1 else ltp
        change = ((ltp - prev) / prev * 100) if prev != 0 else 0
        return {"symbol": sym, "ltp": ltp, "change": change}, None
    except Exception as e:
        print(f"Error fetching {symbol}: {e}")
        return None, str(e)

# --- Bot Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["My Positions"], ["➕ Add New Stock"]]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "Bot Ready! 📈\n\nClick My Positions to view or Add New Stock to add.",
        reply_markup=markup
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = str(update.effective_user.id)
    stocks_db = load_stocks()

    if text == "My Positions":
        user_stocks = stocks_db.get(user_id, [])
        if not user_stocks:
            await update.message.reply_text("No stocks added yet. Click ➕ Add New Stock")
            return
        
        # Fetch all data
        results = []
        for sym in user_stocks:
            data, err = get_stock_data(sym)
            if data:
                results.append(data)
        
        # Sort by % change
        results.sort(key=lambda x: x['change'], reverse=True)
        
        msg = "My Positions - Tap %CHG to sort:\n\n"
        msg += f"{'Symbol':<10} {'LTP':<8} {'%CHG'}\n"
        msg += "-"*30 + "\n"
        for r in results:
            msg += f"{r['symbol']:<10} {r['ltp']:.2f} {r['change']:+.2f}%\n"
        
        # Inline button for sorting (just visual)
        keyboard = [[InlineKeyboardButton("➕ Add New Stock", callback_data="add")]]
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))

    elif text == "➕ Add New Stock" or text == "+ Add New Stock":
        waiting_for_stock.add(user_id)
        await update.message.reply_text("➕ Add New Stock\nSend symbol like: RELIANCE or RELIANCE, TCS, INFY")

    else:
        # User is sending symbols to add
        if user_id in waiting_for_stock or "," in text or text.isupper():
            if user_id in waiting_for_stock:
                waiting_for_stock.discard(user_id)
            
            symbols = [s.strip().upper() for s in text.split(',') if s.strip()]
            added = []
            failed = []
            
            if user_id not in stocks_db:
                stocks_db[user_id] = []
            
            for sym in symbols:
                print(f"USER TYPED: {sym} - Trying to add")
                data, err = get_stock_data(sym)
                if data:
                    if sym not in stocks_db[user_id]:
                        stocks_db[user_id].append(sym)
                        added.append(f"{sym} ({data['ltp']:.2f})")
                    else:
                        added.append(f"{sym} already exists")
                else:
                    failed.append(f"{sym} - {err}")
            
            save_stocks(stocks_db)
            
            reply = ""
            if added:
                reply += "✅ Added: " + ", ".join(added) + "\n"
            if failed:
                reply += "❌ Failed: " + ", ".join(failed) + "\n"
            
            if not reply:
                reply = "Send valid symbols like: EXIDEIND, SUZLON"
            
            await update.message.reply_text(reply)
            # Show positions again
            await handle_message(update, context) if False else None
        else:
            await update.message.reply_text("Click ➕ Add New Stock to add symbols")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "add":
        waiting_for_stock.add(str(query.from_user.id))
        await query.message.reply_text("➕ Add New Stock\nSend symbol like: RELIANCE or RELIANCE, TCS")

# --- Main ---
if __name__ == '__main__':
    print("Bot Running - Fixed + All 3 screens + Flask")
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN not set in Environment Variables!")
    else:
        app_bot = ApplicationBuilder().token(BOT_TOKEN).build()
        app_bot.add_handler(CommandHandler("start", start))
        app_bot.add_handler(CallbackQueryHandler(button_handler))
        app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app_bot.run_polling()
