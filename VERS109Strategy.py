import pandas as pd
import numpy as np

# === 1. Ustawienia strategii ===
EMA_short = 10
EMA_long = 50
RSI_long_thresh = 50
RSI_short_thresh = 50
RR_value = 1.5  # możesz ustawić 1.0, 1.5 lub 2.0
max_trades = 250

# === 2. Obliczanie indykatorów ===
def add_indicators(df):
    df["EMA_short"] = df["Close"].ewm(span=EMA_short, adjust=False).mean()
    df["EMA_long"] = df["Close"].ewm(span=EMA_long, adjust=False).mean()
    df["EMA200"] = df["Close"].ewm(span=200, adjust=False).mean()

    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift()).abs(),
        (df["Low"] - df["Close"].shift()).abs()
    ], axis=1).max(axis=1)
    df["ATR"] = tr.ewm(span=14, adjust=False).mean()

    delta = df["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss
    df["RSI"] = 100 - (100 / (1 + rs))

    df["VolMA20"] = df["Volume"].rolling(20).mean()
    return df.dropna()

# === 3. Funkcja strategii ===
def run_strategy(df, rr_value=RR_value, max_trades=max_trades):
    df = add_indicators(df)
    trades = []
    pos = None

    for i in range(50, len(df)):
        row = df.iloc[i]
        close, ema_s, ema_l, ema200, atr, rsi, volume, vol_ma = (
            row["Close"], row["EMA_short"], row["EMA_long"], row["EMA200"],
            row["ATR"], row["RSI"], row["Volume"], row["VolMA20"]
        )

        # minimalna różnica EMA
        ema_diff_thresh = 0.001 * close

        # brak pozycji -> sprawdzamy sygnał
        if pos is None and atr > df["ATR"].iloc[i-1] and volume > 1.1 * vol_ma:
            if ema_s > ema_l + ema_diff_thresh and close > ema200 and rsi < RSI_long_thresh:
                pos = {"type": "LONG", "entry": close, "sl": close - atr, "tp": close + rr_value * atr}
            elif ema_s < ema_l - ema_diff_thresh and close < ema200 and rsi > RSI_short_thresh:
                pos = {"type": "SHORT", "entry": close, "sl": close + atr, "tp": close - rr_value * atr}

        # sprawdzamy pozycję
        if pos:
            hi, lo = row["High"], row["Low"]
            if pos["type"] == "LONG":
                if hi >= pos["tp"]:
                    trades.append(pos["tp"] - pos["entry"])
                    pos = None
                elif lo <= pos["sl"]:
                    trades.append(pos["sl"] - pos["entry"])
                    pos = None
            elif pos["type"] == "SHORT":
                if lo <= pos["tp"]:
                    trades.append(pos["entry"] - pos["tp"])
                    pos = None
                elif hi >= pos["sl"]:
                    trades.append(pos["entry"] - pos["sl"])
                    pos = None

        if len(trades) >= max_trades:
            break

    return trades

# === 4. Przykład użycia ===
# df powinien być dostarczony przez bota z live datą
# results = run_strategy(df)
# print(results)
