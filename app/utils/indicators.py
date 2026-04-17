import pandas as pd
import pandas_ta as ta


# ─── EMA ────────────────────────────────────────────────────────────────────

def calculate_ema(df: pd.DataFrame, period: int) -> pd.Series:
    return df.ta.ema(length=period)


# ─── MACD ────────────────────────────────────────────────────────────────────

def calculate_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return (macd_line, signal_line, histogram)."""
    result      = df.ta.macd(fast=fast, slow=slow, signal=signal)
    macd_line   = result[f"MACD_{fast}_{slow}_{signal}"]
    signal_line = result[f"MACDs_{fast}_{slow}_{signal}"]
    histogram   = result[f"MACDh_{fast}_{slow}_{signal}"]
    return macd_line, signal_line, histogram


# ─── ATR ─────────────────────────────────────────────────────────────────────

def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return df.ta.atr(length=period)


# ─── Swing High / Low (bahan S/R zone) ───────────────────────────────────────

def find_swing_levels(
    df: pd.DataFrame,
    lookback: int = 500,
    window: int = 4,
) -> tuple[list[float], list[float]]:
    """
    Cari swing high (resistance) dan swing low (support) dari candle history.

    Parameters
    ----------
    df       : DataFrame dengan kolom 'high' dan 'low'
    lookback : jumlah candle terakhir yang ditelusuri
    window   : jumlah candle kiri & kanan yang harus lebih rendah/tinggi

    Returns
    -------
    (res_levels, sup_levels) — list harga mentah sebelum di-cluster
    """
    high_arr = df["high"].values
    low_arr  = df["low"].values

    n        = len(df)
    lb_start = max(0, n - lookback)
    sw       = window

    res_levels: list[float] = []
    sup_levels: list[float] = []

    for i in range(lb_start + sw, n - sw):
        if high_arr[i] == max(high_arr[i - sw: i + sw + 1]):
            res_levels.append(float(high_arr[i]))
        if low_arr[i] == min(low_arr[i - sw: i + sw + 1]):
            sup_levels.append(float(low_arr[i]))

    return res_levels, sup_levels


# ─── Candle Pattern ───────────────────────────────────────────────────────────

def detect_candle_pattern(df: pd.DataFrame) -> tuple[bool, bool]:
    """
    Deteksi candle pattern bullish / bearish pada candle terakhir.
    Pattern yang dikenali: pin bar, engulfing, marubozu.

    Returns
    -------
    (has_bull_pattern, has_bear_pattern)
    """
    close  = float(df["close"].iloc[-1])
    o_val  = float(df["open"].iloc[-1])
    h_val  = float(df["high"].iloc[-1])
    l_val  = float(df["low"].iloc[-1])
    prev_o = float(df["open"].iloc[-2])
    prev_c = float(df["close"].iloc[-2])

    body         = abs(close - o_val)
    up_shad      = h_val - max(o_val, close)
    lo_shad      = min(o_val, close) - l_val
    candle_range = h_val - l_val
    mid_price    = (h_val + l_val) / 2

    # Pin bar
    pin_bull = lo_shad > 2.0 * body and lo_shad > up_shad and close > mid_price
    pin_bear = up_shad > 2.0 * body and up_shad > lo_shad and close < mid_price

    # Engulfing
    eng_bull = (close > o_val and prev_c < prev_o
                and close > prev_o and o_val < prev_c)
    eng_bear = (close < o_val and prev_c > prev_o
                and close < prev_o and o_val > prev_c)

    # Marubozu — body >= 80% range, momentum kuat searah
    maru_bull = close > o_val and candle_range > 0 and body >= candle_range * 0.8
    maru_bear = close < o_val and candle_range > 0 and body >= candle_range * 0.8

    return (pin_bull or eng_bull or maru_bull), (pin_bear or eng_bear or maru_bear)


def classify_candle(df: pd.DataFrame) -> dict:
    """
    Klasifikasi candle terakhir secara lengkap untuk dataset ML.

    Returns
    -------
    dict berisi anatomy candle + nama pattern
    """
    close  = float(df["close"].iloc[-1])
    o_val  = float(df["open"].iloc[-1])
    h_val  = float(df["high"].iloc[-1])
    l_val  = float(df["low"].iloc[-1])
    prev_o = float(df["open"].iloc[-2])
    prev_c = float(df["close"].iloc[-2])

    body      = abs(close - o_val)
    up_shad   = h_val - max(o_val, close)
    lo_shad   = min(o_val, close) - l_val
    candle_range = h_val - l_val
    mid_price = (h_val + l_val) / 2

    # Arah candle
    if body < candle_range * 0.05:
        candle_dir = "doji"
    elif close > o_val:
        candle_dir = "bullish"
    else:
        candle_dir = "bearish"

    # Nama pattern
    pattern_name = "none"

    # Doji
    if candle_dir == "doji":
        pattern_name = "doji"

    # Pin bar bullish (hammer)
    elif lo_shad > 2.0 * body and lo_shad > up_shad and close > mid_price:
        pattern_name = "pin_bar_bull"

    # Pin bar bearish (shooting star)
    elif up_shad > 2.0 * body and up_shad > lo_shad and close < mid_price:
        pattern_name = "pin_bar_bear"

    # Engulfing bullish
    elif (close > o_val and prev_c < prev_o
          and close > prev_o and o_val < prev_c):
        pattern_name = "engulfing_bull"

    # Engulfing bearish
    elif (close < o_val and prev_c > prev_o
          and close < prev_o and o_val > prev_c):
        pattern_name = "engulfing_bear"

    # Marubozu bullish (body >= 80% range, shadow kecil)
    elif close > o_val and body >= candle_range * 0.8:
        pattern_name = "marubozu_bull"

    # Marubozu bearish
    elif close < o_val and body >= candle_range * 0.8:
        pattern_name = "marubozu_bear"

    return {
        "body":         round(body, 5),
        "upper_shadow": round(up_shad, 5),
        "lower_shadow": round(lo_shad, 5),
        "candle_dir":   candle_dir,
        "pattern_name": pattern_name,
    }
