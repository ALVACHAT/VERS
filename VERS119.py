# VERS119_light_BNB_15m.py
# Wymagania:
# pip install yfinance pandas numpy python-telegram-bot==13.15 colorama

import logging
import yfinance as yf
import pandas as pd
import numpy as np
from telegram import Bot
import pytz
import json
import os
from colorama import Fore, init
from datetime import datetime, time
import time as t

# ===== IMPORT STRATEGII =====
from VERS109Strategy import add_indicators, RR_value, EMA_short, EMA_long, RSI_long_thresh, RSI_short_thresh

# ===== INICJALIZACJA =====
init(autoreset=True)
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# ===== TELEGRAM =====
TOKEN = "8084949536:AAGxIZ-h8DPKCi9KuqsbGa3NqyFfzNZoqYI"
CHAT_IDS = [7382335576, 7022309811, 7168430398]
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
                for k in ["BTC", "BNB", "NASDAQ 100", "S&P 500", "NVIDIA", "Gold", "trend"]:
                    if k not in data:
                        data[k] = {}
                return data
            except Exception as e:
                print(Fore.RED + f"Błąd przy wczytywaniu position.json: {e}")
                return {"BTC": {}, "BNB": {}, "NASDAQ 100": {}, "S&P 500": {}, "NVIDIA": {}, "Gold": {}, "trend": {}}
    return {"BTC": {}, "BNB": {}, "NASDAQ 100": {}, "S&P 500": {}, "NVIDIA": {}, "Gold": {}, "trend": {}}

position = load_position()
if "trend" not in position:
    position["trend"] = {}

# ===== FUNKCJE NOTYFIKACJI =====
def notify(text, level="info"):
    color = Fore.YELLOW
    if level == "success": color = Fore.GREEN
    elif level == "error": color = Fore.RED
    print(color + text)
    for chat_id in CHAT_IDS:
        try:
            bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown')
        except Exception as e:
            print(Fore.RED + f"Błąd przy wysyłaniu powiadomienia do {chat_id}: {e}")

def notify_open(name, pos):
    text = (f"Nowa pozycja\n{name} {pos['type']}\n"
            f"Entry: {pos['entry_price']:.4f}\nStop: {pos['stop']:.4f}\nTarget: {pos['target']:.4f}\n"
            f"Lots: {pos.get('lots',0):.6f}\nRSI: {pos.get('RSI',0):.2f}, ATR: {pos.get('ATR',0):.4f}")
    notify(text, "success" if pos['type']=='LONG' else "error")

def notify_exit(name, pos, exit_price, exit_reason):
    try:
        pnl_per_unit = (exit_price - pos['entry_price']) * (1 if pos['type']=='LONG' else -1)
        lot_value = lot_values.get(name, 1)
        pnl = pnl_per_unit * lot_value * pos.get('lots', 0)
        text = (f"Pozycja zamknięta\n{name} {pos['type']} {exit_reason}\n"
                f"Entry: {pos['entry_price']:.4f}\nExit: {exit_price:.4f}\n"
                f"Lots: {pos.get('lots',0):.6f}\nPnL: {pnl:.2f} $\n"
                f"RSI: {pos.get('RSI',0):.2f}, ATR: {pos.get('ATR',0):.4f}")
        notify(text, "success" if exit_reason=='TP' else "error")
    except Exception as e:
        print(Fore.RED + f"Błąd w notify_exit: {e}")

# ===== PARAMETRY RYZYKA =====
risk_per_trade = 25.0
lot_values = {
    "BTC": 48879.99,
    "BNB": 300.0,
    "NASDAQ 100": 24680,
    "S&P 500": 6675,
    "NVIDIA": 177,
    "Gold": 37337
}

# ===== GODZINY RYNKÓW =====
def is_market_open(name):
    now = datetime.utcnow()
    weekday = now.weekday()
    ct = now.time()
    if name in ["BTC", "BNB"]: return True
    if name == "NASDAQ 100": return weekday < 5
    if name in ["NVIDIA", "S&P 500"]: return weekday < 5 and time(13,30) <= ct <= time(20,0)
    if name == "Gold": return weekday < 5 and not (time(22,0) <= ct < time(23,0))
    return False

# ===== STRATEGIA LIVE =====
def check_trades():
    global position
    assets = {
        "BTC-USD": "BTC",
        "BNB-USD": "BNB",
        "^NDX": "NASDAQ 100",
        "^GSPC": "S&P 500",
        "NVDA": "NVIDIA",
        "GC=F": "Gold"
    }

    for symbol, name in assets.items():
        if not is_market_open(name): continue
        try:
            df = yf.download(symbol, period="7d", interval="15m", progress=False, auto_adjust=True)
        except:
            continue
        if df.empty: continue
        df = add_indicators(df)
        if df.empty: continue

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
        except:
            continue

        ema_diff_thresh = 0.001 * close
        if name not in position: position[name] = {}
        pos = position[name]
        is_active = bool(pos) and ("entry_price" in pos)

        # --- Nowe otwarcie ---
        if not is_active and atr > prev_atr and volume > 1.1 * vol_ma:
            # LONG
            if ema_s > ema_l + ema_diff_thresh and close > ema200 and rsi < RSI_long_thresh:
                stop, target = close - atr, close + RR_value * atr
                risk_per_unit = abs(close - stop)
                lot_value = lot_values.get(name, None)
                if not lot_value or risk_per_unit <= 0: continue
                lots = round(risk_per_trade / (risk_per_unit * lot_value), 6)
                if lots <= 0: continue
                position[name] = {'type':'LONG','entry_price':close,'stop':stop,'target':target,
                                  'RSI':rsi,'ATR':atr,'lots':lots,'notified':False}
                save_position(position)
                if not position[name]['notified']:
                    notify_open(name, position[name])
                    position[name]['notified'] = True
                    save_position(position)

            # SHORT
            elif ema_s < ema_l - ema_diff_thresh and close < ema200 and rsi > RSI_short_thresh:
                stop, target = close + atr, close - RR_value * atr
                risk_per_unit = abs(close - stop)
                lot_value = lot_values.get(name, None)
                if not lot_value or risk_per_unit <= 0: continue
                lots = round(risk_per_trade / (risk_per_unit * lot_value), 6)
                if lots <= 0: continue
                position[name] = {'type':'SHORT','entry_price':close,'stop':stop,'target':target,
                                  'RSI':rsi,'ATR':atr,'lots':lots,'notified':False}
                save_position(position)
                if not position[name]['notified']:
                    notify_open(name, position[name])
                    position[name]['notified'] = True
                    save_position(position)

        # --- Zamykanie pozycji ---
        elif is_active:
            exit_price, exit_reason = None, None
            if pos['type']=='LONG':
                if hi >= pos['target']: exit_price, exit_reason = pos['target'], 'TP'
                elif lo <= pos['stop']: exit_price, exit_reason = pos['stop'], 'SL'
            elif pos['type']=='SHORT':
                if lo <= pos['target']: exit_price, exit_reason = pos['target'], 'TP'
                elif hi >= pos['stop']: exit_price, exit_reason = pos['stop'], 'SL'
            if exit_price is not None:
                notify_exit(name,pos,exit_price,exit_reason)
                position[name] = {}
                save_position(position)

# ===== MAIN =====
def main():
    logging.info("Start light 15m bota...")
    check_trades()
    while True:
        t.sleep(60)
        check_trades()

if __name__ == "__main__":
    main()
