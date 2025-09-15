# VERS119.py - pełny skrypt z obsługą komend i powiadomień Telegram + scheduler
# Wymagania:
# pip install yfinance pandas numpy python-telegram-bot==13.15 APScheduler pytz matplotlib colorama

import logging
import yfinance as yf
import pandas as pd
import numpy as np
from telegram import Bot, Update
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

# ===== KONFIGURACJA =====
init(autoreset=True)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# → WSTAW TU SWÓJ TOKEN I CHAT_ID:
TOKEN = "TWOJ_TOKEN_TUTAJ"   # ← zamień na token bota
CHAT_ID = 123456789          # ← zamień na swój chat_id (liczba)

# Bezpieczeństwo: spróbuj stworzyć instancję bota (nie przerywamy jeśli nie działa)
try:
    bot = Bot(TOKEN)
except Exception as e:
    bot = None
    logging.error(f"Nie udało się utworzyć obiektu Bot: {e}")

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
            except Exception:
                return {"BTC": {}, "NASDAQ 100": {}, "S&P 500": {}, "trend": {}}
    return {"BTC": {}, "NASDAQ 100": {}, "S&P 500": {}, "trend": {}}

position = load_position()
if "trend" not in position:
    position["trend"] = {}

# ===== FUNKCJE POMOCNICZE / NOTYFIKACJE =====
def notify(text, level="info"):
    """Wysyła powiadomienie na Telegram (jeśli bot poprawnie zainicjowany) i loguje do konsoli."""
    if level == "success":
        print(Fore.GREEN + text)
    elif level == "error":
        print(Fore.RED + text)
    else:
        print(Fore.YELLOW + text)
    if bot is None:
        logging.error("Bot nie jest zainicjowany - brak wysyłki do Telegrama.")
        return
    try:
        bot.send_message(chat_id=CHAT_ID, text=text)
    except Exception as e:
        logging.error(f"Błąd przy wysyłaniu powiadomienia: {e}")

def notify_open(name, pos):
    text = (f"📈 Nowa pozycja\n"
            f"{name} {pos['type']}\n"
            f"Entry: {pos['entry_price']:.4f}\n"
            f"Stop: {pos['stop']:.4f}\n"
            f"Target: {pos['target']:.4f}\n"
            f"RSI: {pos.get('RSI14','-')}, ATR: {pos.get('ATR14','-')}")
    level = "success" if pos['type'] == 'LONG' else "error"
    notify(text, level)

def notify_exit(name, pos, exit_price, exit_reason):
    pnl = (exit_price - pos['entry_price']) * (1 if pos['type'] == 'LONG' else -1)
    text = (f"📉 Pozycja zamknięta\n"
            f"{name} {pos['type']} {exit_reason}\n"
            f"Entry: {pos['entry_price']:.4f}\n"
            f"Exit: {exit_price:.4f}\n"
            f"PnL: {pnl:.4f}\n"
            f"RSI: {pos.get('RSI14','-')}, ATR: {pos.get('ATR14','-')}")
    level = "success" if exit_reason == 'TP' else "error"
    notify(text, level)

# ===== INDIKATORY =====
def compute_indicators(df):
    df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()
    df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()
    df['VWAP'] = (df['Close'] * df['Volume']).cumsum() / df['Volume'].cumsum()
    tr = pd.concat([df['High']-df['Low'],
                    (df['High']-df['Close'].shift(1)).abs(),
                    (df['Low']-df['Close'].shift(1)).abs()], axis=1).max(axis=1)
    df['ATR14'] = tr.ewm(span=14, adjust=False).mean()
    delta = df['Close'].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    df['RSI14'] = 100 - (100 / (1 + rs))
    return df.dropna()

# ===== LOGIKA STRATEGII =====
def check_trades():
    """Sprawdza sygnały i otwiera/zamyka pozycje; wywoływane okresowo i na żądanie."""
    global position
    try:
        assets = {"BTC-USD":"BTC","^NDX":"NASDAQ 100","^GSPC":"S&P 500"}
        for symbol,name in assets.items():
            # pobierz dane
            df_15m = yf.download(symbol, period="2d", interval="15m", progress=False, auto_adjust=True)
            df_h1 = yf.download(symbol, period="7d", interval="1h", progress=False, auto_adjust=True)
            if df_15m.empty or df_h1.empty:
                logging.warning(f"Brak danych dla {symbol}")
                continue

            df_15m = compute_indicators(df_15m)
            df_h1 = compute_indicators(df_h1)
            last_15m = df_15m.iloc[-1]
            last_h1 = df_h1.iloc[-1]

            trend_h1 = "LONG" if float(last_h1['EMA20'])>float(last_h1['EMA50']) else "SHORT"

            close = float(last_15m['Close'])
            ema20 = float(last_15m['EMA20'])
            ema50 = float(last_15m['EMA50'])
            atr = float(last_15m['ATR14'])
            rsi = float(last_15m['RSI14'])
            vwap = float(last_15m['VWAP'])
            hi = float(last_15m['High'])
            lo = float(last_15m['Low'])

            long_cond = trend_h1=="LONG" and ema20>ema50 and close<=vwap and rsi<53
            short_cond = trend_h1=="SHORT" and ema20<ema50 and close>=vwap and rsi>47

            if name not in position:
                position[name] = {}

            pos = position[name]

            # otwieranie
            if not pos and long_cond:
                stop = close - atr
                target = close + 2*atr
                position[name] = {'type':'LONG','entry_price':close,'stop':stop,'target':target,
                                  'entry_time':str(last_15m.name),'EMA20':ema20,'EMA50':ema50,'RSI14':rsi,'ATR14':atr}
                save_position(position)
                notify_open(name, position[name])

            elif not pos and short_cond:
                stop = close + atr
                target = close - 2*atr
                position[name] = {'type':'SHORT','entry_price':close,'stop':stop,'target':target,
                                  'entry_time':str(last_15m.name),'EMA20':ema20,'EMA50':ema50,'RSI14':rsi,'ATR14':atr}
                save_position(position)
                notify_open(name, position[name])

            # zamykanie
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
    except Exception as e:
        logging.exception(f"Błąd w check_trades: {e}")

# ===== KOMENDY TELEGRAM =====
def start_command(update: Update, context: CallbackContext):
    update.message.reply_text("Bot uruchomiony. Wysyłam sygnały i przyjmuję komendy: /check /status /charts")

def check_command(update: Update, context: CallbackContext):
    update.message.reply_text("Ręczne sprawdzenie sygnałów... (może chwilę potrwać)")
    try:
        check_trades()
        update.message.reply_text("Sprawdzenie zakończone.")
    except Exception as e:
        update.message.reply_text(f"Błąd podczas sprawdzania: {e}")

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

def plot_chart(df, name, trend_h1):
    plt.style.use('dark_background')
    fig, (ax1, ax2) = plt.subplots(2,1,figsize=(12,8), gridspec_kw={'height_ratios':[3,1]}, sharex=True)

    ax1.plot(df['Close'], label='Close')
    ax1.plot(df['EMA20'], label='EMA20', linestyle='--')
    ax1.plot(df['EMA50'], label='EMA50', linestyle='-.')
    ax1.plot(df['VWAP'], label='VWAP', linestyle=':')

    prev_long, prev_short = False, False
    trades = []

    for idx in df.index:
        row = df.loc[idx]
        close = float(row['Close'])
        ema20 = float(row['EMA20'])
        ema50 = float(row['EMA50'])
        vwap = float(row['VWAP'])
        rsi = float(row['RSI14'])
        atr = float(row['ATR14'])

        if trend_h1=="LONG":
            long_cond = ema20>ema50 and close<=vwap and rsi<53
            short_cond = False
        elif trend_h1=="SHORT":
            long_cond = False
            short_cond = ema20<ema50 and close>=vwap and rsi>47
        else:
            long_cond = ema20>ema50 and close<=vwap and rsi<53
            short_cond = ema20<ema50 and close>=vwap and rsi>47

        if long_cond and not prev_long:
            tp = close + 2*atr
            sl = close - atr
            ax1.scatter(idx, close, color='lime', s=60, marker='^')
            trades.append({'type':'LONG','entry':close,'tp':tp,'sl':sl,'entry_time':idx})

        if short_cond and not prev_short:
            tp = close - 2*atr
            sl = close + atr
            ax1.scatter(idx, close, color='red', s=60, marker='v')
            trades.append({'type':'SHORT','entry':close,'tp':tp,'sl':sl,'entry_time':idx})

        prev_long = long_cond
        prev_short = short_cond

    ax2.plot(df['RSI14'], label='RSI14')
    ax2.axhline(70, linestyle='--', alpha=0.5)
    ax2.axhline(30, linestyle='--', alpha=0.5)

    ax1.legend(loc='upper left', fontsize=8)
    ax2.legend(loc='upper left', fontsize=8)
    fig.suptitle(f'{name} - 15m chart', color='white')
    buf=io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)
    plt.close(fig)
    return buf

def charts_command(update: Update, context: CallbackContext):
    assets={"BTC-USD":"BTC","^NDX":"NASDAQ 100","^GSPC":"S&P 500"}
    for symbol,name in assets.items():
        df_15m=yf.download(symbol, period="2d", interval="15m", progress=False, auto_adjust=True)
        df_h1=yf.download(symbol, period="7d", interval="1h", progress=False, auto_adjust=True)
        if df_15m.empty or df_h1.empty:
            continue
        df_15m=compute_indicators(df_15m)
        df_h1=compute_indicators(df_h1)
        last_h1=df_h1.iloc[-1]
        trend_h1="LONG" if float(last_h1['EMA20'])>float(last_h1['EMA50']) else "SHORT"
        buf=plot_chart(df_15m,name,trend_h1)
        update.message.reply_photo(photo=buf, caption=f"{name} - 15m chart with potential entries")

# ===== URUCHOMIENIE BOTA I SCHEDULERA =====
def main():
    logging.info("Start bota VERS119...")
    updater = Updater(TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    # Handlery
    dispatcher.add_handler(CommandHandler("start", start_command))
    dispatcher.add_handler(CommandHandler("check", check_command))
    dispatcher.add_handler(CommandHandler("status", status_command))
    dispatcher.add_handler(CommandHandler("charts", charts_command))

    # Scheduler - co 15 minut
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_trades,'interval',minutes=15,timezone=pytz.timezone('Europe/Warsaw'))
    scheduler.start()

    # Uruchom natychmiastowe sprawdzenie przy starcie
    try:
        check_trades()
    except Exception as e:
        logging.error(f"Pierwotne check_trades wyrzuciło błąd: {e}")

    # Polling Telegram
    updater.start_polling()
    logging.info("Bot działa. Polling uruchomiony.")
    updater.idle()

if __name__ == "__main__":
    main()
