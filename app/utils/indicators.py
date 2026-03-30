import pandas as pd
import pandas_ta as ta

# ─── RSI ────────────────────────────────────────────────────────────────────

def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return df.ta.rsi(length=period)


def rsi_signal(rsi: pd.Series, oversold: float = 30, overbought: float = 70) -> str:
    last = rsi.iloc[-1]
    if last <= oversold:
        return "buy"
    if last >= overbought:
        return "sell"
    return "hold"


# ─── MA (EMA) ────────────────────────────────────────────────────────────────

def calculate_ema(df: pd.DataFrame, period: int) -> pd.Series:
    return df.ta.ema(length=period)


def ma_signal(df: pd.DataFrame, fast: int = 9, slow: int = 21) -> str:
    """Return 'buy' saat EMA fast cross above slow, 'sell' saat cross below."""
    ema_fast = calculate_ema(df, fast)
    ema_slow = calculate_ema(df, slow)
    prev_fast, prev_slow = ema_fast.iloc[-2], ema_slow.iloc[-2]
    curr_fast, curr_slow = ema_fast.iloc[-1], ema_slow.iloc[-1]

    if prev_fast <= prev_slow and curr_fast > curr_slow:
        return "buy"
    if prev_fast >= prev_slow and curr_fast < curr_slow:
        return "sell"
    return "hold"


# ─── MACD ────────────────────────────────────────────────────────────────────

def calculate_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return (macd_line, signal_line, histogram)."""
    result = df.ta.macd(fast=fast, slow=slow, signal=signal)
    macd_line   = result[f"MACD_{fast}_{slow}_{signal}"]
    signal_line = result[f"MACDs_{fast}_{slow}_{signal}"]
    histogram   = result[f"MACDh_{fast}_{slow}_{signal}"]
    return macd_line, signal_line, histogram


def macd_signal(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> str:
    """Return 'buy' saat MACD cross above signal line, 'sell' saat cross below."""
    macd_line, signal_line, _ = calculate_macd(df, fast, slow, signal)
    prev_macd, prev_sig = macd_line.iloc[-2], signal_line.iloc[-2]
    curr_macd, curr_sig = macd_line.iloc[-1], signal_line.iloc[-1]

    if prev_macd <= prev_sig and curr_macd > curr_sig:
        return "buy"
    if prev_macd >= prev_sig and curr_macd < curr_sig:
        return "sell"
    return "hold"


# ─── Bollinger Bands ─────────────────────────────────────────────────────────

def calculate_bbands(
    df: pd.DataFrame, period: int = 20, std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return (upper, middle, lower)."""
    result = df.ta.bbands(length=period, std=std)
    upper  = result[f"BBU_{period}_{std}_{std}"]
    middle = result[f"BBM_{period}_{std}_{std}"]
    lower  = result[f"BBL_{period}_{std}_{std}"]
    return upper, middle, lower


# ─── ATR ─────────────────────────────────────────────────────────────────────

def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return df.ta.atr(length=period)


# ─── Combined Signal ─────────────────────────────────────────────────────────

def combined_signal(df: pd.DataFrame) -> dict:
    """
    Gabungkan RSI + MA + MACD dengan trend filter EMA 200.
    Return dict berisi signal + semua nilai numerik indikator
    untuk disimpan ke DB history / data ML.
    """
    rsi                          = calculate_rsi(df)
    macd_line, signal_line, hist = calculate_macd(df)
    ema_fast                     = calculate_ema(df, 9)
    ema_slow                     = calculate_ema(df, 21)
    ema_200                      = calculate_ema(df, 200)
    bb_upper, bb_mid, bb_lower   = calculate_bbands(df)
    atr                          = calculate_atr(df)

    rsi_sig  = rsi_signal(rsi)
    ma_sig   = ma_signal(df)
    macd_sig = macd_signal(df)

    signals = [rsi_sig, ma_sig, macd_sig]
    buys    = signals.count("buy")
    sells   = signals.count("sell")

    # Trend filter: hanya buy kalau harga di atas EMA 200, hanya sell kalau di bawah
    close       = float(df["close"].iloc[-1])
    trend_up    = close > float(ema_200.iloc[-1])
    trend_down  = close < float(ema_200.iloc[-1])

    if buys >= 2 and trend_up:
        action = "buy"
    elif sells >= 2 and trend_down:
        action = "sell"
    else:
        action = "hold"

    return {
        "signal": action,
        # RSI
        "rsi": round(float(rsi.iloc[-1]), 4),
        "rsi_signal": rsi_sig,
        # EMA
        "ema_fast": round(float(ema_fast.iloc[-1]), 4),
        "ema_slow": round(float(ema_slow.iloc[-1]), 4),
        "ema_200": round(float(ema_200.iloc[-1]), 4),
        "ma_signal": ma_sig,
        # MACD
        "macd": round(float(macd_line.iloc[-1]), 4),
        "macd_signal_line": round(float(signal_line.iloc[-1]), 4),
        "macd_histogram": round(float(hist.iloc[-1]), 4),
        "macd_signal": macd_sig,
        # Bollinger Bands
        "bb_upper": round(float(bb_upper.iloc[-1]), 4),
        "bb_middle": round(float(bb_mid.iloc[-1]), 4),
        "bb_lower": round(float(bb_lower.iloc[-1]), 4),
        # ATR
        "atr": round(float(atr.iloc[-1]), 4),
        # Trend
        "trend": "up" if trend_up else "down",
    }
