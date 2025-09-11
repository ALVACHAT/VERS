# VERS_119_colored.py
# Wymagania: pip install yfinance pandas numpy python-telegram-bot==13.15 APScheduler pytz matplotlib colorama

import logging
import yfinance as yf
import pandas as pd
import numpy as np
from telegram import Update, Bot
from telegram.ext import Updater, CommandHandler, CallbackContext
from apscheduler.schedulers.background import BackgroundScheduler
import pytz
import json
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import io
from colorama import Fore, Style, init

# ===== STRATEGIA =====
from VERS109Strategy import run_strategy, add_indicators

# ===== INICJALIZACJA COLORAMA =====
init(autoreset=True)

# ===== LOGOWANIE =====
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ===== TELEGRAM =====
TOKEN = "8084949536:AAHbOoC6gLH36DrvFpRQQBCJHBZs4bjtEoA"
CHAT_ID = 7382335576
bot = Bot(TOKEN)

# ===== PLIK DO POZYCJI =====
POSITION_FILE = "position.json"

def save_position(position):
    with open(POSITION_FILE, 'w') as f:
        json.dump(position, f, default=str)

def load_position():
    if os.path.exists(POSITION_FILE):
        with open(POSITION_FILE, 'r') as f:
            try:
                return json.load(f)
            except:
                return {"BTC": {}, "NASDAQ 100": {}, "S&P 500": {}, "trend": {}}
    return {"BTC": {}, "NASDAQ 100": {}, "S&P 500": {}, "trend": {}}

position = load_position()
if "trend" not in position:
    position["trend"] = {}

# ===== POWIADOMIENIA =====
def notify(text, level="info"):
    if level=="success":
        print(Fore.GREEN + text)
    elif level=="error":
        print(Fore.RED + text)
    else:
        print(Fore.YELLOW + text)
    try:
        bot.send_message(chat_id=CHAT_ID, text=text, parse_mode='Markdown')
    except Exception as e:
        print(Fore.RED + f"[ERROR] Błąd przy wysyłaniu powiadomienia: {e}")

def notify_exit(name, pos, exit_price, exit_reason):
    pnl = (exit_price-pos['entry_price'])*(1 if pos['type']=='LONG' else -1)
    text = (f"Pozycja zamknięta\n"
            f"{name} {pos['type']} {exit_reason}\n"
            f"Entry: {pos['entry_price']:.4f}\n"
            f"Exit: {exit_price:.4f}\n"
            f"PnL: {pnl:.4f}\n"
            f"RSI: {pos.get('RSI',0):.2f}, ATR: {pos.get('ATR',0):.4f}")
    level = "success" if exit_reason=='TP' else "error"
    notify(text, level)

def notify_open(name, pos):
    text = (f"Nowa pozycja\n"
            f"{name} {pos['type']}\n"
            f"Entry: {pos['entry_price']:.4f}\n"
            f"Stop: {pos['sl']:.4f}\n"
            f"Target: {pos['tp']:.4f}\n"
            f"RSI: {pos.get('RSI',0):.2f}, ATR: {pos.get('ATR',0):.4f}")
    level = "success" if pos['type']=='LONG' else "error"
    notify(text, level)

# ===== SPRAWDZANIE POZYCJI (NOWA STRATEGIA Z TP/SL) =====
def check_trades():
    global position
    assets = {"BTC-USD":"BTC","^NDX":"NASDAQ 100","^GSPC":"S&P 500"}

    for symbol, name in assets.items():
        df_15m = yf.download(symbol, period="2d", interval="15m", progress=False, auto_adjust=True)
        if df_15m.empty:
            print(Fore.RED + f"[ERROR] Brak danych dla {name}")
            continue

        df_15m = add_indicators(df_15m)
        last_row = df_15m.iloc[-1]
        close = last_row["Close"]
        atr = last_row["ATR"]
        rsi = last_row["RSI"]

        pos = position.get(name, {})

        # SPRAWDZENIE ZAMKNIĘCIA TP/SL
        if pos:
            hi, lo = last_row["High"], last_row["Low"]
            exit_price, exit_reason = None, None
            if pos["type"] == "LONG":
                if hi >= pos["tp"]:
                    exit_price, exit_reason = pos["tp"], 'TP'
                elif lo <= pos["sl"]:
                    exit_price, exit_reason = pos["sl"], 'SL'
            elif pos["type"] == "SHORT":
                if lo <= pos["tp"]:
                    exit_price, exit_reason = pos["tp"], 'TP'
                elif hi >= pos["sl"]:
                    exit_price, exit_reason = pos["sl"], 'SL'

            if exit_price is not None:
                notify_exit(name, pos, exit_price, exit_reason)
                position[name] = {}
                save_position(position)
                pos = None

        # SPRAWDZENIE OTWARCIA NOWEJ POZYCJI
        if not pos:
            trades = run_strategy(df_15m)
            if trades:
                pos_type = "LONG" if trades[-1] > 0 else "SHORT"
                sl = close - atr if pos_type=="LONG" else close + atr
                tp = close + atr if pos_type=="LONG" else close - atr
                position[name] = {
                    "type": pos_type,
                    "entry_price": close,
                    "sl": sl,
                    "tp": tp,
                    "entry_time": str(last_row.name),
                    "ATR": atr,
                    "RSI": rsi
                }
                save_position(position)
                notify_open(name, position[name])
            else:
                print(Fore.YELLOW + f"[INFO] Brak sygnału dla {name}")

# ===== KOMENDY TELEGRAM =====
def start_command(update: Update, context: CallbackContext):
    update.message.reply_text("VERS119 uruchomiony, Wysyła sygnały LONG/SHORT.")

def check_command(update: Update, context: CallbackContext):
    update.message.reply_text("VERS jest online")

def status_command(update: Update, context: CallbackContext):
    text = "Aktualne pozycje:\n\n"
    for name,pos in position.items():
        if name=="trend": continue
        if pos:
            arrow = "LONG" if pos['type']=='LONG' else "SHORT"
            text += f"{name} {arrow}\nEntry: {pos['entry_price']:.4f}\nStop: {pos['sl']:.4f}\nTarget: {pos['tp']:.4f}\n\n"
        else:
            text += f"{name}\nBrak aktywnej pozycji\n\n"
    update.message.reply_text(text)

def plot_chart(df, name, trend_h1):
    plt.style.use('dark_background')
    fig, (ax1, ax2) = plt.subplots(2,1,figsize=(12,8), gridspec_kw={'height_ratios':[3,1]}, sharex=True)

    ax1.plot(df['Close'], label='Close', color='magenta', linewidth=1.5)
    ax1.plot(df['EMA_short'], label='EMA_short', color='pink', linestyle='--')
    ax1.plot(df['EMA_long'], label='EMA_long', color='hotpink', linestyle='-.')
    ax1.plot(df['EMA200'], label='EMA200', color='violet', linestyle=':')

    ax2.plot(df['RSI'], label='RSI', color='magenta', linewidth=1.5)
    ax2.axhline(70, color='pink', linestyle='--', alpha=0.5)
    ax2.axhline(30, color='pink', linestyle='--', alpha=0.5)
    ax2.set_ylabel('RSI', color='white')
    ax2.set_xlabel('Time', color='white')
    ax2.tick_params(colors='white')
    ax2.legend(loc='upper left', fontsize=8)
    ax2.grid(True, color='gray', linestyle='--', alpha=0.3)

    ax1.set_ylabel('Price', color='white')
    ax1.tick_params(colors='white')
    ax1.legend(loc='upper left', fontsize=8)
    ax1.grid(True, color='gray', linestyle='--', alpha=0.3)

    fig.suptitle(f'{name} - 15m chart with potential entries', color='white')
    buf=io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)
    plt.close(fig)
    return buf

def charts_command(update: Update, context: CallbackContext):
    assets={"BTC-USD":"BTC","^NDX":"NASDAQ 100","^GSPC":"S&P 500"}
    for symbol,name in assets.items():
        df_15m=yf.download(symbol, period="2d", interval="15m", progress=False, auto_adjust=True)
        if df_15m.empty: continue
        df_15m=add_indicators(df_15m)
        last_h1=df_15m.iloc[-1]
        trend_h1="LONG" if float(last_h1['EMA_short'])>float(last_h1['EMA_long']) else "SHORT"
        buf=plot_chart(df_15m,name,trend_h1)
        update.message.reply_photo(photo=buf, caption=f"{name} - 15m chart with potential entries")

# ===== HANDLER BŁĘDÓW =====
from telegram.error import NetworkError, TelegramError

def error_handler(update: object, context: CallbackContext):
    try:
        raise context.error
    except NetworkError as e:
        print(Fore.RED + f"[NETWORK ERROR] Brak połączenia z Telegramem: {e}")
    except TelegramError as e:
        print(Fore.RED + f"[TELEGRAM ERROR] Problem z API Telegrama: {e}")
    except Exception as e:
        print(Fore.RED + f"[UNHANDLED ERROR] {e}")

# ===== URUCHOMIENIE BOTA =====
def main():
    logging.info("Start bota...")
    updater=Updater(TOKEN,use_context=True)
    dispatcher=updater.dispatcher

    dispatcher.add_handler(CommandHandler("start",start_command))
    dispatcher.add_handler(CommandHandler("check",check_command))
    dispatcher.add_handler(CommandHandler("status",status_command))
    dispatcher.add_handler(CommandHandler("charts",charts_command))
    dispatcher.add_error_handler(error_handler)

    scheduler=BackgroundScheduler()
    scheduler.add_job(check_trades,'interval',minutes=15,timezone=pytz.timezone('Europe/Warsaw'))
    scheduler.start()

    print(Fore.GREEN + "[INFO] Bot uruchomiony i nasłuchuje...")

    updater.start_polling()
    updater.idle()

if __name__=="__main__":
    main()
