import pandas as pd
import numpy as np
import yfinance as yf
import logging
from telegram import Bot
import json
import os

# ===== KONFIGURACJA TELEGRAM =====
TOKEN = "TWOJ_TOKEN"       # ← tu wstaw token bota
CHAT_ID = 123456789        # ← tu wstaw swój chat_id
bot = Bot(TOKEN)

# ===== PLIK DO POZYCJI =====
POSITION_FILE = "position.json"

def save_position(position):
    with open(POSITION_FILE, "w") as f:
        json.dump(position, f, default=str)

def load_position():
    if os.path.exists(POSITION_FILE):
        with open(POSITION_FILE, "r") as f:
            try:
                return json.load(f)
            except:
                return {"BTC": {}, "NASDAQ": {}, "SP500": {}, "trend": {}}
    return {"BTC": {}, "NASDAQ": {}, "SP500": {}, "trend": {}}

position = load_position()
if "trend" not in position:
    position["trend"] = {}

# ===== FUNKCJE NOTYFIKACJI =====
def notify(text, level="info"):
    """Wysyła powiadomienie na Telegrama i print do konsoli"""
    print(text)
    try:
        bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="Markdown")
    except Exception as e:
        print(f"[BŁĄD Telegram] {e}")

def notify_open(name, pos):
    text = (f"📈 Nowa pozycja\n"
            f"{name} {pos['type']}\n"
            f"Entry: {pos['entry_price']:.2f}\n"
            f"Stop: {pos['stop']:.2f}\n"
            f"Target: {pos['target']:.2f}")
    notify(text, "success")

def notify_exit(name, pos, exit_price, exit_reason):
    pnl = (exit_price - pos['entry_price']) * (1 if pos['type'] == "LONG" else -1)
    text = (f"📉 Pozycja zamknięta ({exit_reason})\n"
            f"{name} {pos['type']}\n"
            f"Entry: {pos['entry_price']:.2f}\n"
            f"Exit: {exit_price:.2f}\n"
            f"P&L: {pnl:.2f}")
    notify(text, "info")

# ===== STRATEGIA / INDIKATORY =====
def compute_indicators(df):
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
    return df

def check_trades():
    global position
    assets = {"BTC-USD": "BTC", "^NDX": "NASDAQ", "^GSPC": "SP500"}

    for symbol, name in assets.items():
        df = yf.download(symbol, period="2d", interval="15m", progress=False, auto_adjust=True)
        if df.empty:
            continue
        df = compute_indicators(df)
        last = df.iloc[-1]

        close = float(last["Close"])
        ema20 = float(last["EMA20"])
        ema50 = float(last["EMA50"])

        long_cond = ema20 > ema50
        short_cond = ema20 < ema50

        if name not in position:
            position[name] = {}

        pos = position[name]

        if not pos and long_cond:
            stop = close * 0.98
            target = close * 1.04
            position[name] = {
                "type": "LONG",
                "entry_price": close,
                "stop": stop,
                "target": target
            }
            save_position(position)
            notify_open(name, position[name])

        elif not pos and short_cond:
            stop = close * 1.02
            target = close * 0.96
            position[name] = {
                "type": "SHORT",
                "entry_price": close,
                "stop": stop,
                "target": target
            }
            save_position(position)
            notify_open(name, position[name])

        elif pos:
            hi, lo = float(last["High"]), float(last["Low"])
            exit_price, exit_reason = None, None

            if pos["type"] == "LONG":
                if hi >= pos["target"]:
                    exit_price, exit_reason = pos["target"], "TP"
                elif lo <= pos["stop"]:
                    exit_price, exit_reason = pos["stop"], "SL"
            elif pos["type"] == "SHORT":
                if lo <= pos["target"]:
                    exit_price, exit_reason = pos["target"], "TP"
                elif hi >= pos["stop"]:
                    exit_price, exit_reason = pos["stop"], "SL"

            if exit_price:
                notify_exit(name, pos, exit_price, exit_reason)
                position[name] = {}
                save_position(position)

# ===== URUCHOMIENIE =====
if __name__ == "__main__":
    logging.info("Startuję sprawdzanie sygnałów...")
    check_trades()
