# VERS119_full_nocharts.py
# Wymagania:
# pip install yfinance pandas numpy python-telegram-bot==13.15 APScheduler pytz colorama

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
from colorama import Fore, Style, init

# ===== IMPORT STRATEGII =====
from VERS109Strategy import add_indicators, RR_value, EMA_short, EMA_long, RSI_long_thresh, RSI_short_thresh

# ===== INICJALIZACJA =====
init(autoreset=True)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ===== TELEGRAM =====
TOKEN = "8084949536:AAGxIZ-h8DPKCi9KuqsbGa3NqyFfzNZoqYI"
CHAT_ID = 7382335576
bot = Bot(TOKEN)

# ===== PLIK DO POZYCJI =====
POSITION_FILE = "position.json"

def save_position(position):
    try:
        with open(POSITION_FILE, 'w') as f:
            json.dump(position, f, default=str)
    except Exception as e:
        print(Fore.RED + f"Nie udało się zapisać position.json: {e}")

def load_position():
    if os.path.exists(POSITION_FILE):
        with open(POSITION_FILE, 'r') as f:
            try:
                data = json.load(f)
                # upewnij się, że klucze są
                for k in ["BTC", "NASDAQ 100", "S&P 500", "trend"]:
                    if k not in data:
                        data[k] = {}
                return data
            except Exception as e:
                print(Fore.RED + f"Błąd przy wczytywaniu position.json: {e}")
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

def notify_open(name, pos):
    text = (f"Nowa pozycja\n"
            f"{name} {pos['type']}\n"
            f"Entry: {pos['entry_price']:.4f}\n"
            f"Stop: {pos['stop']:.4f}\n"
            f"Target: {pos['target']:.4f}\n"
            f"Lots: {pos.get('lots',0):.6f}\n"
            f"RSI: {pos.get('RSI',0):.2f}, ATR: {pos.get('ATR',0):.4f}")
    level = "success" if pos['type']=='LONG' else "error"
    notify(text, level)

def notify_exit(name, pos, exit_price, exit_reason):
    try:
        pnl_per_unit = (exit_price - pos['entry_price']) * (1 if pos['type']=='LONG' else -1)
        lot_value = lot_values.get(name, 1)
        pnl = pnl_per_unit * lot_value * pos.get('lots', 0)
        text = (f"Pozycja zamknięta\n"
                f"{name} {pos['type']} {exit_reason}\n"
                f"Entry: {pos['entry_price']:.4f}\n"
                f"Exit: {exit_price:.4f}\n"
                f"Lots: {pos.get('lots',0):.6f}\n"
                f"PnL: {pnl:.2f} $\n"
                f"RSI: {pos.get('RSI',0):.2f}, ATR: {pos.get('ATR',0):.4f}")
        level = "success" if exit_reason=='TP' else "error"
        notify(text, level)
    except Exception as e:
        print(Fore.RED + f"Błąd w notify_exit: {e}")
        # i tak spróbuj wysłać prostą wiadomość
        notify(f"Pozycja zamknięta {name} {exit_reason}. (błąd szczegółów: {e})", "info")

# ===== PARAMETRY RYZYKA =====
risk_per_trade = 25.0  # max strata na trade

lot_values = {
    "BTC": 48879.99,
    "NASDAQ 100": 1033,
    "S&P 500": 281
}

# ===== STRATEGIA LIVE =====
def check_trades():
    global position
    assets = {"BTC-USD":"BTC","^NDX":"NASDAQ 100","^GSPC":"S&P 500"}

    for symbol,name in assets.items():
        try:
            df = yf.download(symbol, period="7d", interval="15m", progress=False, auto_adjust=True)
        except Exception as e:
            print(Fore.RED + f"Błąd w yf.download dla {symbol}: {e}")
            continue

        if df.empty:
            print(f"[{name}] brak danych z yfinance.")
            continue

        # sprawdzamy świecę (czy jest nowa)
        last_time = df.index[-1]
        now = pd.Timestamp.now(tz=pytz.timezone("Europe/Warsaw"))
        if (now - last_time).total_seconds() > 20*60:
            # brak świeżej świecy - pomijamy
            print(f"Rynek {name} zamknięty lub brak nowej świecy (ostatnia: {last_time}). Pomijam.")
            continue

        # dodajemy indykatory (musi być po pobraniu świecy)
        df = add_indicators(df)
        if df.empty:
            print(Fore.RED + f"[{name}] add_indicators zwróciło puste df.")
            continue

        last = df.iloc[-1]
        prev = df.iloc[-2]

        # ===== konwersja na float (last to Series) =====
        try:
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
        except Exception as e:
            print(Fore.RED + f"[{name}] Błąd konwersji wartości ze świecy: {e}")
            continue

        ema_diff_thresh = 0.001 * close

        if name not in position:
            position[name] = {}

        pos = position[name]

        # --- Otwieranie ---
        # dodatkowa walidacja: pos powinien mieć 'entry_price' aby być uznany jako aktywny
        is_active = bool(pos) and ("entry_price" in pos)

        if not is_active and atr > prev_atr and volume > 1.1 * vol_ma:
            # LONG
            if ema_s > ema_l + ema_diff_thresh and close > ema200 and rsi < RSI_long_thresh:
                stop = close - atr
                target = close + RR_value * atr

                risk_per_unit = abs(close - stop)
                lot_value = lot_values.get(name, None)

                if lot_value is None:
                    print(Fore.RED + f"[{name}] Brak wartości lot_values dla instrumentu.")
                    continue

                lot_risk = risk_per_unit * lot_value
                if lot_risk <= 0 or np.isnan(lot_risk):
                    print(Fore.RED + f"[{name}] lot_risk nieprawidłowy ({lot_risk}), pomijam otwarcie.")
                    continue

                lots = round(risk_per_trade / lot_risk, 6)
                if lots <= 0:
                    print(Fore.YELLOW + f"[{name}] Obliczone lots = 0, pomijam otwarcie (entry={close}, stop={stop}).")
                    continue

                # zapisz pełną pozycję
                position[name] = {
                    'type':'LONG',
                    'entry_price': close,
                    'stop': stop,
                    'target': target,
                    'RSI': rsi,
                    'ATR': atr,
                    'lots': lots
                }
                save_position(position)
                notify_open(name, position[name])

            # SHORT
            elif ema_s < ema_l - ema_diff_thresh and close < ema200 and rsi > RSI_short_thresh:
                stop = close + atr
                target = close - RR_value * atr

                risk_per_unit = abs(close - stop)
                lot_value = lot_values.get(name, None)
                if lot_value is None:
                    print(Fore.RED + f"[{name}] Brak wartości lot_values dla instrumentu.")
                    continue

                lot_risk = risk_per_unit * lot_value
                if lot_risk <= 0 or np.isnan(lot_risk):
                    print(Fore.RED + f"[{name}] lot_risk nieprawidłowy ({lot_risk}), pomijam otwarcie.")
                    continue

                lots = round(risk_per_trade / lot_risk, 6)
                if lots <= 0:
                    print(Fore.YELLOW + f"[{name}] Obliczone lots = 0, pomijam otwarcie (entry={close}, stop={stop}).")
                    continue

                position[name] = {
                    'type':'SHORT',
                    'entry_price': close,
                    'stop': stop,
                    'target': target,
                    'RSI': rsi,
                    'ATR': atr,
                    'lots': lots
                }
                save_position(position)
                notify_open(name, position[name])

        # --- Zamykanie ---
        elif is_active:
            exit_price, exit_reason = None, None
            try:
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
            except Exception as e:
                print(Fore.RED + f"[{name}] Błąd przy sprawdzaniu TP/SL: {e}")
                exit_price = None

            if exit_price is not None:
                notify_exit(name,pos,exit_price,exit_reason)
                # usuń pozycję i zapisz
                position[name] = {}
                save_position(position)

# ===== KOMENDY TELEGRAM =====
def start_command(update: Update, context: CallbackContext):
    text = (
        "◻ VERS119 - Cudo techniki stworzone przez V-Max Blood C.O i zespół techników "
        "inżynierii sztucznej inteligencji 5o w Orsku.\n"
        "VERS analizuje rynki co 15 minut czasu GMT+2 i sprawdza czy wszystkie warunki "
        "VERS109Strategy na otwarcie pozycji są spełnione. Wysyła pozycje na Telegrama.\n\n"
        "Nowoczesna technologia obliczania wielkości pozycji aby SL = 25$.\n"
        "Dostępne 3 instrumenty: BTC, Nasdaq 100 i S&P 500.\n\n"
        "Bot przeszedł backtest na rynkach 2020-2025 i osiągnął winratio na poziomie "
        "54-57.2% przy RR 1:1.5 ◻"
    )
    update.message.reply_text(text)

def check_command(update: Update, context: CallbackContext):
    update.message.reply_text("◼VERS jest online◼")

def status_command(update: Update, context: CallbackContext):
    text = "📊 Aktualne pozycje:\n\n"
    for name,pos in position.items():
        if name=="trend":
            continue
        text += f"➖ {name}\n"
        # uznajemy aktywną pozycję tylko wtedy gdy ma entry_price
        if pos and "entry_price" in pos:
            arrow = "✔ LONG" if pos['type']=='LONG' else "✔ SHORT"
            text += (f"{arrow}\n"
                     f"Entry: {pos['entry_price']:.4f}\n"
                     f"Stop: {pos['stop']:.4f}\n"
                     f"Target: {pos['target']:.4f}\n"
                     f"Lots: {pos.get('lots',0):.6f}\n\n")
        else:
            text += "✖ Brak aktywnej pozycji\n\n"
    update.message.reply_text(text)

# ===== MAIN =====
def main():
    logging.info("Start bota...")
    updater = Updater(TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start_command))
    dispatcher.add_handler(CommandHandler("check", check_command))
    dispatcher.add_handler(CommandHandler("status", status_command))

    scheduler = BackgroundScheduler()
    scheduler.add_job(check_trades, 'interval', minutes=15, timezone=pytz.timezone('Europe/Warsaw'))
    scheduler.start()
    # natychmiastowe sprawdzenie przy starcie
    check_trades()

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()

