# VERS119.py
# Bot korzystający ze strategii z VERS109Strategy.py
# Wymagania:
# pip install yfinance pandas numpy python-telegram-bot==13.15 APScheduler pytz matplotlib colorama

import logging
import yfinance as yf
import pandas as pd
import numpy as np
import json
import os
import pytz
import io
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from colorama import Fore, Style, init

from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext
from apscheduler.schedulers.background import BackgroundScheduler

# --- Import strategii (plik VERS109Strategy.py powinien być w tym samym folderze) ---
# Funkcja add_indicators + parametry są używane do decyzji wejścia
try:
    from VERS109Strategy import add_indicators, RR_value, EMA_short, EMA_long, EMA200, RSI_long_thresh, RSI_short_thresh
except Exception as e:
    # Jeśli import się nie powiedzie, podaj domyślne wartości kompatybilne ze strategią
    add_indicators = None
    RR_value = 1.5
    EMA_short = 10
    EMA_long = 50
    EMA200 = 200
    RSI_long_thresh = 50
    RSI_short_thresh = 50
    logging.warning(f"Nie udało się zaimportować VERS109Strategy: {e}. Będą użyte wartości domyślne.")

# ===== Konfiguracja i logging =====
init(autoreset=True)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# → Wstaw tutaj swój token i chat_id:
TOKEN = "8084949536:AAGxIZ-h8DPKCi9KuqsbGa3NqyFfzNZoqYI"   # <- zamień
CHAT_ID = 123456789          # <- zamień

# Bezpieczna inicjalizacja bota (logujemy, jeśli nie działa)
try:
    bot = Bot(TOKEN)
except Exception as e:
    bot = None
    logging.error(f"Nie udało się utworzyć obiektu Bot: {e}")

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
                return {"BTC": {}, "NASDAQ": {}, "SP500": {}, "trend": {}}
    return {"BTC": {}, "NASDAQ": {}, "SP500": {}, "trend": {}}

position = load_position()
if "trend" not in position:
    position["trend"] = {}

# ===== Funkcje powiadomień =====
def notify(text, level="info"):
    if level == "success":
        print(Fore.GREEN + text)
    elif level == "error":
        print(Fore.RED + text)
    else:
        print(Fore.YELLOW + text)
    if bot is None:
        logging.error("Bot nie jest zainicjowany, nie wysyłam Telegrama.")
        return
    try:
        bot.send_message(chat_id=CHAT_ID, text=text)
    except Exception as e:
        logging.error(f"Błąd wysyłania powiadomienia: {e}")

def notify_open(name, pos):
    text = (f"📈 Nowa pozycja\n"
            f"{name} {pos['type']}\n"
            f"Entry: {pos['entry']:.6f}\n"
            f"SL: {pos['sl']:.6f}\n"
            f"TP: {pos['tp']:.6f}\n"
            f"RR: {pos.get('rr', RR_value)}")
    notify(text, "success")

def notify_exit(name, pos, exit_price, exit_reason):
    pnl = (exit_price - pos['entry']) * (1 if pos['type'] == 'LONG' else -1)
    text = (f"📉 Pozycja zamknięta ({exit_reason})\n"
            f"{name} {pos['type']}\n"
            f"Entry: {pos['entry']:.6f}\n"
            f"Exit: {exit_price:.6f}\n"
            f"P&L: {pnl:.6f}")
    notify(text, "info")

# ===== Logika strategii live (oparta na VERS109Strategy) =====
def check_trades():
    """
    Pobiera dane dla kilku instrumentów, liczy indykatory funkcją add_indicators
    z VERS109Strategy.py i otwiera/zamyka pozycje używając tej samej logiki co w run_strategy.
    """
    global position
    try:
        if add_indicators is None:
            logging.error("Funkcja add_indicators nie jest dostępna. Upewnij się, że VERS109Strategy.py jest obok i poprawny.")
            return

        assets = {"BTC-USD": "BTC", "^NDX": "NASDAQ", "^GSPC": "SP500"}

        for symbol, name in assets.items():
            # pobieramy wystarczająco dużo świec (min 60) żeby add_indicators miało dane
            df = yf.download(symbol, period="7d", interval="15m", progress=False, auto_adjust=True)
            if df.empty or len(df) < 60:
                logging.warning(f"Za mało danych dla {symbol}.")
                continue

            # adaptacja: funkcja add_indicators w VERS109Strategy zwraca df.dropna()
            df_ind = add_indicators(df.copy())
            if df_ind.empty:
                logging.warning(f"add_indicators zwróciło puste dla {symbol}.")
                continue

            # potrzebujemy dostęp do ostatnich dwóch wierszy (ostatnia świeca i poprzednia)
            last_i = df_ind.index[-1]
            prev_i = df_ind.index[-2]

            last = df_ind.loc[last_i]
            prev = df_ind.loc[prev_i]

            close = float(last["Close"])
            atr = float(last["ATR"])
            prev_atr = float(prev["ATR"])
            volume = float(last["Volume"])
            vol_ma = float(last["VolMA20"])
            ema_s = float(last["EMA_short"])
            ema_l = float(last["EMA_long"])
            ema200 = float(last["EMA200"])
            rsi = float(last["RSI"])

            # warunki wejścia z VERS109Strategy:
            #    - atr > prev_atr
            #    - volume > 1.1 * vol_ma
            #    - minimalna różnica EMA: ema_diff_thresh = 0.001 * close
            #    - LONG: ema_s > ema_l + thresh and close > ema200 and rsi < RSI_long_thresh
            #    - SHORT: ema_s < ema_l - thresh and close < ema200 and rsi > RSI_short_thresh

            ema_diff_thresh = 0.001 * close

            # ensure position slot exists
            if name not in position:
                position[name] = {}

            pos = position[name]

            # otwieranie pozycji
            if (not pos) and (atr > prev_atr) and (volume > 1.1 * vol_ma):
                if (ema_s > ema_l + ema_diff_thresh) and (close > ema200) and (rsi < RSI_long_thresh):
                    # otwórz LONG
                    sl = close - atr
                    tp = close + RR_value * atr
                    position[name] = {"type": "LONG", "entry": close, "sl": sl, "tp": tp, "rr": RR_value, "entry_time": str(last_i)}
                    save_position(position)
                    notify_open(name, position[name])

                elif (ema_s < ema_l - ema_diff_thresh) and (close < ema200) and (rsi > RSI_short_thresh):
                    # otwórz SHORT
                    sl = close + atr
                    tp = close - RR_value * atr
                    position[name] = {"type": "SHORT", "entry": close, "sl": sl, "tp": tp, "rr": RR_value, "entry_time": str(last_i)}
                    save_position(position)
                    notify_open(name, position[name])

            # sprawdzanie zamknięcia pozycji
            elif pos:
                hi = float(last["High"])
                lo = float(last["Low"])
                exit_price = None
                exit_reason = None

                if pos["type"] == "LONG":
                    if hi >= pos["tp"]:
                        exit_price, exit_reason = pos["tp"], "TP"
                    elif lo <= pos["sl"]:
                        exit_price, exit_reason = pos["sl"], "SL"
                else:  # SHORT
                    if lo <= pos["tp"]:
                        exit_price, exit_reason = pos["tp"], "TP"
                    elif hi >= pos["sl"]:
                        exit_price, exit_reason = pos["sl"], "SL"

                if exit_price is not None:
                    notify_exit(name, pos, exit_price, exit_reason)
                    position[name] = {}
                    save_position(position)

    except Exception as e:
        logging.exception(f"Błąd w check_trades: {e}")

# ===== Komendy Telegram =====
def start_command(update: Update, context: CallbackContext):
    update.message.reply_text("Bot VERS119 uruchomiony. Strategia: VERS109Strategy (EMA_short/EMA_long, EMA200, ATR, RSI, Vol). Komendy: /check /status /charts")

def check_command(update: Update, context: CallbackContext):
    update.message.reply_text("Ręczne sprawdzenie sygnałów (live)...")
    try:
        check_trades()
        update.message.reply_text("Sprawdzono sygnały.")
    except Exception as e:
        update.message.reply_text(f"Błąd during check: {e}")

def status_command(update: Update, context: CallbackContext):
    text = "Aktualne pozycje:\n\n"
    for name, pos in position.items():
        if name == "trend":
            continue
        if pos:
            arrow = "LONG" if pos["type"] == "LONG" else "SHORT"
            text += f"{name} {arrow}\nEntry: {pos['entry']:.6f}\nSL: {pos['sl']:.6f}\nTP: {pos['tp']:.6f}\n\n"
        else:
            text += f"{name}\nBrak aktywnej pozycji\n\n"
    update.message.reply_text(text)

def plot_chart(df, name):
    plt_style_backup = plt.rcParams.copy()
    try:
        plt.style.use('dark_background')
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), gridspec_kw={'height_ratios': [3, 1]}, sharex=True)
        ax1.plot(df['Close'], label='Close')
        if 'EMA_short' in df.columns:
            ax1.plot(df['EMA_short'], label=f'EMA{EMA_short}', linestyle='--')
        if 'EMA_long' in df.columns:
            ax1.plot(df['EMA_long'], label=f'EMA{EMA_long}', linestyle='-.')
        if 'EMA200' in df.columns:
            ax1.plot(df['EMA200'], label='EMA200', linestyle=':')
        if 'VolMA20' in df.columns and 'Volume' in df.columns:
            ax1.legend(loc='upper left', fontsize=8)

        ax2.plot(df['RSI'], label='RSI')
        ax2.axhline(70, linestyle='--', alpha=0.5)
        ax2.axhline(30, linestyle='--', alpha=0.5)

        ax1.set_ylabel('Price')
        ax2.set_ylabel('RSI')
        ax2.set_xlabel('Time')

        fig.suptitle(f'{name} - 15m', color='white')
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        plt.close(fig)
        return buf
    finally:
        plt.rcParams.update(plt_style_backup)

def charts_command(update: Update, context: CallbackContext):
    assets = {"BTC-USD": "BTC", "^NDX": "NASDAQ", "^GSPC": "SP500"}
    for symbol, name in assets.items():
        df = yf.download(symbol, period="7d", interval="15m", progress=False, auto_adjust=True)
        if df.empty:
            continue
        if add_indicators is not None:
            df_ind = add_indicators(df.copy())
        else:
            df_ind = df
        buf = plot_chart(df_ind, name)
        update.message.reply_photo(photo=buf, caption=f"{name} - strategia VERS109")

# ===== Uruchomienie bota i schedulera =====
def main():
    logging.info("Start bota VERS119 (strategia: VERS109Strategy)...")
    updater = Updater(TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start_command))
    dispatcher.add_handler(CommandHandler("check", check_command))
    dispatcher.add_handler(CommandHandler("status", status_command))
    dispatcher.add_handler(CommandHandler("charts", charts_command))

    # scheduler co 15 minut
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_trades, 'interval', minutes=15, timezone=pytz.timezone('Europe/Warsaw'))
    scheduler.start()

    # pierwsze sprawdzenie przy starcie
    try:
        check_trades()
    except Exception as e:
        logging.error(f"Pierwotne check_trades wyrzuciło błąd: {e}")

    updater.start_polling()
    logging.info("Bot działa. Polling uruchomiony.")
    updater.idle()

if __name__ == "__main__":
    main()
