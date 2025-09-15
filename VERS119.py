# VERS119.py
# Wymagania:
# pip install yfinance pandas numpy python-telegram-bot==13.15 APScheduler pytz matplotlib colorama

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

# ===== IMPORT STRATEGII =====
from VERS109Strategy import add_indicators, RR_value, EMA_short, EMA_long, RSI_long_thresh, RSI_short_thresh

# ===== INICJALIZACJA =====
init(autoreset=True)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ===== TELEGRAM =====
TOKEN = "8084949536:AAGxIZ-h8DPKCi9KuqsbGa3NqyFfzNZoqYI"      # <--- wstaw swój token
CHAT_ID = 123456789       # <--- wstaw swoje chat_id
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

# ===== FUNKCJE NOTYFIKACJI =====
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
        print(Fore.RED + f"Błąd przy wysyłaniu powiadomienia: {e}")

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
            f"Stop: {pos['stop']:.4f}\n"
            f"Target: {pos['target']:.4f}\n"
            f"RSI: {pos.get('RSI',0):.2f}, ATR: {pos.get('ATR',0):.4f}")
    level = "success" if pos['type']=='LONG' else "error"
    notify(text, level)

# ===== STRATEGIA LIVE =====
def check_trades():
    global position
    assets = {"BTC-USD":"BTC","^NDX":"NASDAQ 100","^GSPC":"S&P 500"}

    for symbol,name in assets.items():
        df = yf.download(symbol, period="7d", interval="15m", progress=False, auto_adjust=True)
        if df.empty: 
            continue

        df = add_indicators(df)
        if df.empty: 
            continue

        last = df.iloc[-1]
        prev = df.iloc[-2]

        close = float(last["Close"])
        ema_s = float(last["EMA_short"])
        ema_l = float(last["EMA_long"])
        ema200 = float(last["EMA200"])
        atr = float(last["ATR"])
        prev_atr = float(prev["ATR"])
        rsi = float(last["RSI"])
        volume = float(last["Volume"])
        vol_ma = float(last["VolMA20"])
        hi, lo = float(last["High"]), float(last["Low"])

        ema_diff_thresh = 0.001 * close

        if name not in position:
            position[name] = {}

        pos = position[name]

        # --- Otwieranie ---
        if not pos and atr > prev_atr and volume > 1.1 * vol_ma:
            if ema_s > ema_l + ema_diff_thresh and close > ema200 and rsi < RSI_long_thresh:
                stop = close - atr
                target = close + RR_value * atr
                position[name] = {'type':'LONG','entry_price':close,'stop':stop,'target':target,
                                  'RSI':rsi,'ATR':atr}
                save_position(position)
                notify_open(name, position[name])

            elif ema_s < ema_l - ema_diff_thresh and close < ema200 and rsi > RSI_short_thresh:
                stop = close + atr
                target = close - RR_value * atr
                position[name] = {'type':'SHORT','entry_price':close,'stop':stop,'target':target,
                                  'RSI':rsi,'ATR':atr}
                save_position(position)
                notify_open(name, position[name])

        # --- Zamykanie ---
        elif pos:
            exit_price, exit_reason = None, None
            if pos['type']=='LONG':
                if hi >= pos['target']:
                    exit_price, exit_reason = pos['target'],'TP'
                elif lo <= pos['stop']:
                    exit_price, exit_reason = pos['stop'],'SL'
            elif pos['type']=='SHORT':
                if lo <= pos['target']:
                    exit_price, exit_reason = pos['target'],'TP'
                elif hi >= pos['stop']:
                    exit_price, exit_reason = pos['stop'],'SL'

            if exit_price is not None:
                notify_exit(name,pos,exit_price,exit_reason)
                position[name] = {}
                save_position(position)

# ===== KOMENDY TELEGRAM =====
def start_command(update: Update, context: CallbackContext):
    update.message.reply_text("Bot FlipsRSI uruchomiony! Wysyła sygnały LONG/SHORT.")

def check_command(update: Update, context: CallbackContext):
    update.message.reply_text("Bot aktywny.")

def status_command(update: Update, context: CallbackContext):
    text = "Aktualne pozycje:\n\n"
    for name,pos in position.items():
        if name=="trend": continue
        if pos:
            arrow = "LONG" if pos['type']=='LONG' else "SHORT"
            text += f"{name} {arrow}\nEntry: {pos['entry_price']:.4f}\nStop: {pos['stop']:.4f}\nTarget: {pos['target']:.4f}\n\n"
        else:
            text += f"{name}\nBrak aktywnej pozycji\n\n"
    update.message.reply_text(text)

def plot_chart(df, name):
    plt.style.use('dark_background')
    fig, (ax1, ax2) = plt.subplots(2,1,figsize=(12,8), gridspec_kw={'height_ratios':[3,1]}, sharex=True)

    ax1.plot(df['Close'], label='Close', color='magenta', linewidth=1.5)
    ax1.plot(df['EMA_short'], label=f'EMA{EMA_short}', color='pink', linestyle='--')
    ax1.plot(df['EMA_long'], label=f'EMA{EMA_long}', color='hotpink', linestyle='-.')
    ax1.plot(df['EMA200'], label='EMA200', color='violet', linestyle=':')

    ax2.plot(df['RSI'], label='RSI', color='magenta', linewidth=1.5)
    ax2.axhline(70, color='pink', linestyle='--', alpha=0.5)
    ax2.axhline(30, color='pink', linestyle='--', alpha=0.5)

    ax1.set_ylabel('Price', color='white')
    ax2.set_ylabel('RSI', color='white')
    ax2.set_xlabel('Time', color='white')

    ax1.legend(loc='upper left', fontsize=8)
    ax2.legend(loc='upper left', fontsize=8)
    ax1.grid(True, color='gray', linestyle='--', alpha=0.3)
    ax2.grid(True, color='gray', linestyle='--', alpha=0.3)

    fig.suptitle(f'{name} - 15m chart with potential entries', color='white')
    buf=io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)
    plt.close(fig)
    return buf

def charts_command(update: Update, context: CallbackContext):
    assets={"BTC-USD":"BTC","^NDX":"NASDAQ 100","^GSPC":"S&P 500"}
    for symbol,name in assets.items():
        df=yf.download(symbol, period="7d", interval="15m", progress=False, auto_adjust=True)
        if df.empty: continue
        df=add_indicators(df)
        buf=plot_chart(df,name)
        update.message.reply_photo(photo=buf, caption=f"{name} - 15m chart with potential entries")

# ===== MAIN =====
def main():
    logging.info("Start bota...")
    updater=Updater(TOKEN,use_context=True)
    dispatcher=updater.dispatcher

    dispatcher.add_handler(CommandHandler("start",start_command))
    dispatcher.add_handler(CommandHandler("check",check_command))
    dispatcher.add_handler(CommandHandler("status",status_command))
    dispatcher.add_handler(CommandHandler("charts",charts_command))

    scheduler=BackgroundScheduler()
    scheduler.add_job(check_trades,'interval',minutes=15,timezone=pytz.timezone('Europe/Warsaw'))
    scheduler.start()

    updater.start_polling()
    updater.idle()

if __name__=="__main__":
    main()

