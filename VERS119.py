# VERS119_notify_trades_loop.py
# Wymagania:
# pip install yfinance pandas numpy python-telegram-bot==13.15 colorama APScheduler pytz

import yfinance as yf
from datetime import datetime, time
from colorama import Fore, init
from telegram import Bot
import json
import os
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

# ===== INICJALIZACJA =====
init(autoreset=True)

# ===== TELEGRAM =====
TOKEN = "8084949536:AAGxIZ-h8DPKCi9KuqsbGa3NqyFfzNZoqYI"
CHAT_IDS = [7382335576, 7022309811, 7168430398]
bot = Bot(TOKEN)

# ===== PLIK POZYCJI =====
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
                for k in ["BTC", "NASDAQ 100", "S&P 500", "NVIDIA", "Gold"]:
                    if k not in data:
                        data[k] = {}
                return data
            except:
                pass
    return {"BTC": {}, "NASDAQ 100": {}, "S&P 500": {}, "NVIDIA": {}, "Gold": {}}

position = load_position()

# ===== FUNKCJE NOTYFIKACJI =====
def notify(text, level="info"):
    if level=="success":
        print(Fore.GREEN + text)
    elif level=="error":
        print(Fore.RED + text)
    else:
        print(Fore.YELLOW + text)
    for chat_id in CHAT_IDS:
        try:
            bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown')
        except Exception as e:
            print(Fore.RED + f"Błąd przy wysyłaniu powiadomienia do {chat_id}: {e}")

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
        lot_value = pos.get('lot_value', 1)
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

# ===== PARAMETRY =====
risk_per_trade = 25.0

lot_values = {
    "BTC": 48879.99,
    "NASDAQ 100": 24680,
    "S&P 500": 6675,
    "NVIDIA": 177,
    "Gold": 37337
}

# ===== STRATEGIA =====
from VERS109Strategy import add_indicators, RR_value, EMA_short, EMA_long, RSI_long_thresh, RSI_short_thresh

# ===== GODZINY RYNKU =====
def is_market_open(name):
    now = datetime.utcnow()
    weekday = now.weekday()
    current_time = now.time()
    if name == "BTC":
        return True
    elif name == "NASDAQ 100":
        return weekday < 5
    elif name in ["NVIDIA", "S&P 500"]:
        if weekday >= 5: return False
        return time(13,30) <= current_time <= time(20,0)
    elif name == "Gold":
        if weekday >= 5: return False
        return not (time(22,0) <= current_time < time(23,0))
    return False

# ===== LOGIKA TREJDÓW =====
def check_trades():
    global position
    assets = {
        "BTC-USD": "BTC",
        "^NDX": "NASDAQ 100",
        "^GSPC": "S&P 500",
        "NVDA": "NVIDIA",
        "GC=F": "Gold"
    }

    for symbol, name in assets.items():
        if not is_market_open(name):
            print(f"[{name}] rynek poza godzinami aktywności. Pomijam.")
            continue

        try:
            # Pobieramy dane bez auto_adjust
            df = yf.download(symbol, period="7d", interval="15m", progress=False, auto_adjust=False)
        except Exception as e:
            print(Fore.RED + f"Błąd w yf.download dla {symbol}: {e}")
            continue

        if df.empty:
            print(f"[{name}] brak danych z yfinance.")
            continue

        df = add_indicators(df)
        if df.empty:
            print(Fore.RED + f"[{name}] add_indicators zwróciło puste df.")
            continue

        last = df.iloc[[-1]]
        prev = df.iloc[[-2]]

        try:
            close = float(last["Close"].iloc[0])
            hi, lo = float(last["High"].iloc[0]), float(last["Low"].iloc[0])
            ema_s = float(last["EMA_short"].iloc[0])
            ema_l = float(last["EMA_long"].iloc[0])
            ema200 = float(last["EMA200"].iloc[0])
            atr = float(last["ATR"].iloc[0])
            prev_atr = float(prev["ATR"].iloc[0])
            rsi = float(last["RSI"].iloc[0])
            volume = float(last["Volume"].iloc[0])
            vol_ma = float(last["VolMA20"].iloc[0])
        except Exception as e:
            print(Fore.RED + f"[{name}] Błąd konwersji wartości ze świecy: {e}")
            continue

        ema_diff_thresh = 0.001 * close
        if name not in position:
            position[name] = {}

        pos = position[name]
        is_active = bool(pos) and ("entry_price" in pos)

        # --- Nowe otwarcie ---
        if not is_active and atr > prev_atr and volume > 1.1 * vol_ma:
            # LONG
            if ema_s > ema_l + ema_diff_thresh and close > ema200 and rsi < RSI_long_thresh:
                stop = close - atr
                target = close + RR_value * atr
                risk_per_unit = abs(close - stop)
                lot_value = lot_values.get(name, None)
                if not lot_value or risk_per_unit <= 0:
                    continue
                lots = round(risk_per_trade / (risk_per_unit * lot_value), 6)
                if lots <= 0:
                    continue
                position[name] = {
                    'type': 'LONG',
                    'entry_price': close,
                    'stop': stop,
                    'target': target,
                    'RSI': rsi,
                    'ATR': atr,
                    'lots': lots,
                    'notified': False
                }
                save_position(position)
                if not position[name]['notified']:
                    notify_open(name, position[name])
                    position[name]['notified'] = True
                    save_position(position)

            # SHORT
            elif ema_s < ema_l - ema_diff_thresh and close < ema200 and rsi > RSI_short_thresh:
                stop = close + atr
                target = close - RR_value * atr
                risk_per_unit = abs(close - stop)
                lot_value = lot_values.get(name, None)
                if not lot_value or risk_per_unit <= 0:
                    continue
                lots = round(risk_per_trade / (risk_per_unit * lot_value), 6)
                if lots <= 0:
                    continue
                position[name] = {
                    'type': 'SHORT',
                    'entry_price': close,
                    'stop': stop,
                    'target': target,
                    'RSI': rsi,
                    'ATR': atr,
                    'lots': lots,
                    'notified': False
                }
                save_position(position)
                if not position[name]['notified']:
                    notify_open(name, position[name])
                    position[name]['notified'] = True
                    save_position(position)

        # --- Zamykanie pozycji ---
        elif is_active:
            exit_price, exit_reason = None, None
            try:
                if pos['type'] == 'LONG':
                    if hi >= pos['target']:
                        exit_price, exit_reason = pos['target'], 'TP'
                    elif lo <= pos['stop']:
                        exit_price, exit_reason = pos['stop'], 'SL'
                elif pos['type'] == 'SHORT':
                    if lo <= pos['target']:
                        exit_price, exit_reason = pos['target'], 'TP'
                    elif hi >= pos['stop']:
                        exit_price, exit_reason = pos['stop'], 'SL'

                # Zaktualizowane logowanie dla debugowania
                print(f"[DEBUG] {name} | hi={hi:.2f}, lo={lo:.2f}, entry={pos['entry_price']:.2f}, stop={pos['stop']:.2f}, target={pos['target']:.2f}")
            except Exception as e:
                print(Fore.RED + f"[{name}] Błąd przy sprawdzaniu TP/SL: {e}")

            if exit_price is not None:
                notify_exit(name, pos, exit_price, exit_reason)
                position[name] = {}
                save_position(position)

# ===== MAIN =====
if __name__ == "__main__":
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_trades, 'interval', minutes=1, timezone=pytz.timezone('Europe/Warsaw'))
    scheduler.start()
    print(Fore.YELLOW + "Bot uruchomiony, analizuje rynek co 1 minutę...")
    
    # Utrzymanie działania w tle
    try:
        import time
        while True:
            time.sleep(10)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        print(Fore.RED + "Bot zatrzymany")
