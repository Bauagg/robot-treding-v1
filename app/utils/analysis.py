import numpy as np
import pandas as pd

from app.utils.indicators import (
    calculate_ema,
    calculate_macd,
    calculate_atr,
    find_swing_levels,
    detect_candle_pattern,
)

# Slope threshold dari sweep backtest: WR 50%, PF 1.445
SLOPE_THRESHOLD = 0.00025


def cluster_zones(levels: list[float]) -> list[float]:
    """Cluster S/R levels yang berdekatan (dalam 10 pip) jadi satu zona."""
    if not levels:
        return []
    sl = sorted(levels)
    zones, grp = [], [sl[0]]
    for p in sl[1:]:
        if p - grp[-1] <= 0.0010:
            grp.append(p)
        else:
            zones.append(float(np.mean(grp)))
            grp = [p]
    zones.append(float(np.mean(grp)))
    return zones


def analyze_h1(df_h1: pd.DataFrame) -> dict:
    """
    Hitung trend H1 (EMA50 slope + posisi vs EMA200)
    dan cari S/R zone dari 500 candle terakhir.

    Returns dict berisi data H1 + _sup_zones + _res_zones (raw, untuk analyze_m15).
    """
    ema50  = calculate_ema(df_h1, 50)
    ema200 = calculate_ema(df_h1, 200)
    atr_h1 = calculate_atr(df_h1, 14)

    e50        = float(ema50.iloc[-1])
    e200       = float(ema200.iloc[-1])
    atr_val_h1 = float(atr_h1.iloc[-1])
    slope      = float(ema50.iloc[-1]) - float(ema50.iloc[-4])
    close      = float(df_h1["close"].iloc[-1])

    # Trend kuat: slope threshold + EMA50 vs EMA200 + close vs EMA200
    if slope > SLOPE_THRESHOLD and close > e200 and e50 > e200:
        trend = "up"
    elif slope < -SLOPE_THRESHOLD and close < e200 and e50 < e200:
        trend = "down"
    else:
        trend = "sideways"

    # S/R Zone dari 720 candle H1 terakhir (~1 bulan)
    res_levels, sup_levels = find_swing_levels(df_h1, lookback=720, window=4)
    res_zones = cluster_zones(res_levels)
    sup_zones = cluster_zones(sup_levels)

    # Cek proximity harga ke zona (1.0x ATR H1)
    thr          = 1.0 * atr_val_h1
    in_resistance = any(abs(close - z) <= thr for z in res_zones)
    in_support    = any(abs(close - z) <= thr for z in sup_zones)

    return {
        "open_h1":      round(float(df_h1["open"].iloc[-1]), 4),
        "high_h1":      round(float(df_h1["high"].iloc[-1]), 4),
        "low_h1":       round(float(df_h1["low"].iloc[-1]), 4),
        "close_h1":     round(close, 4),
        "volume_h1":    round(float(df_h1["volume"].iloc[-1]), 2),
        "trend_h1":     trend,
        "ema_50_h1":    round(e50, 4),
        "ema_200_h1":   round(e200, 4),
        "in_support":   in_support,
        "in_resistance":in_resistance,
        "atr_h1":       round(atr_val_h1, 5),
        # raw zones untuk dipakai di analyze_m15
        "_sup_zones":   sup_zones,
        "_res_zones":   res_zones,
    }


def analyze_m15(df_m15: pd.DataFrame, sup_zones: list, res_zones: list) -> dict:
    """
    Cek 3 komponen entry M15:
    1. MACD histogram arah
    2. EMA9 vs EMA21 posisi
    3. Candle pattern (pin bar / engulfing)
    """
    close = float(df_m15["close"].iloc[-1])
    o_val = float(df_m15["open"].iloc[-1])
    h_val = float(df_m15["high"].iloc[-1])
    l_val = float(df_m15["low"].iloc[-1])

    atr     = calculate_atr(df_m15, 14)
    atr_val = float(atr.iloc[-1])

    _, _, histogram = calculate_macd(df_m15)
    hist_curr = float(histogram.iloc[-1])
    hist_prev = float(histogram.iloc[-2])
    macd_up   = hist_curr > hist_prev and hist_curr > 0
    macd_down = hist_curr < hist_prev and hist_curr < 0

    ema9  = calculate_ema(df_m15, 9)
    ema21 = calculate_ema(df_m15, 21)
    e9    = float(ema9.iloc[-1])
    e21   = float(ema21.iloc[-1])

    has_bull_pattern, has_bear_pattern = detect_candle_pattern(df_m15)

    thr_m15         = 1.5 * atr_val
    near_support    = any(abs(close - z) <= thr_m15 for z in sup_zones)
    near_resistance = any(abs(close - z) <= thr_m15 for z in res_zones)
    macd_slope      = round(hist_curr - hist_prev, 6)

    return {
        "open_m15":            round(o_val, 4),
        "high_m15":            round(h_val, 4),
        "low_m15":             round(l_val, 4),
        "close_m15":           round(close, 4),
        "volume_m15":          round(float(df_m15["volume"].iloc[-1]), 2),
        "ema_9_m15":           round(e9, 4),
        "ema_21_m15":          round(e21, 4),
        "ema_bias":            "buy" if e9 > e21 else ("sell" if e9 < e21 else "hold"),
        "macd_histogram_m15":  round(hist_curr, 6),
        "macd_slope":          macd_slope,
        "macd_up":             macd_up,
        "macd_down":           macd_down,
        "has_bull_pattern":    has_bull_pattern,
        "has_bear_pattern":    has_bear_pattern,
        "near_support_m15":    near_support,
        "near_resistance_m15": near_resistance,
        "atr_m15":             round(atr_val, 5),
        "macd_bias":           "buy" if macd_up else ("sell" if macd_down else "hold"),
    }


def get_confluence_score(action: str, h1: dict, m15: dict) -> int:
    """
    Hitung confluence score (0-5) untuk arah buy atau sell.
    Return score, atau 0 kalau kondisi dasar tidak terpenuhi.
    """
    trend  = h1["trend_h1"]
    in_sup = h1["in_support"]
    in_res = h1["in_resistance"]

    if action == "buy" and trend == "up" and in_sup:
        score = 2
        if m15["macd_up"]:               score += 1
        if m15["ema_bias"] == "buy":     score += 1
        if m15["has_bull_pattern"]:      score += 1
        return score

    if action == "sell" and trend == "down" and in_res:
        score = 2
        if m15["macd_down"]:             score += 1
        if m15["ema_bias"] == "sell":    score += 1
        if m15["has_bear_pattern"]:      score += 1
        return score

    return 0
