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


def analyze_d1(df_d1: pd.DataFrame) -> str:
    """
    Trend D1 — filter arah besar jangka panjang.
    Return "up" / "down" / "sideways"
    Syarat: slope wajib + salah satu (close vs EMA200 ATAU EMA50 vs EMA200).
    """
    try:
        ema50  = calculate_ema(df_d1, 50)
        ema200 = calculate_ema(df_d1, 200)
        e50    = float(ema50.iloc[-2])
        e200   = float(ema200.iloc[-2])
        slope  = float(ema50.iloc[-2]) - float(ema50.iloc[-5])
        close  = float(df_d1["close"].iloc[-2])

        if slope > SLOPE_THRESHOLD and (close > e200 or e50 > e200):
            return "up"
        if slope < -SLOPE_THRESHOLD and (close < e200 or e50 < e200):
            return "down"
    except Exception:
        pass
    return "sideways"


def analyze_h4(df_h4: pd.DataFrame) -> str:
    """
    Trend H4 — konfirmasi arah besar.
    Return "up" / "down" / "sideways"
    Syarat: slope wajib + salah satu (close vs EMA200 ATAU EMA50 vs EMA200).
    """
    try:
        ema50  = calculate_ema(df_h4, 50)
        ema200 = calculate_ema(df_h4, 200)
        e50    = float(ema50.iloc[-2])
        e200   = float(ema200.iloc[-2])
        slope  = float(ema50.iloc[-2]) - float(ema50.iloc[-5])
        close  = float(df_h4["close"].iloc[-2])

        if slope > SLOPE_THRESHOLD and (close > e200 or e50 > e200):
            return "up"
        if slope < -SLOPE_THRESHOLD and (close < e200 or e50 < e200):
            return "down"
    except Exception:
        pass
    return "sideways"


def analyze_h1(df_h1: pd.DataFrame) -> dict:
    """
    Hitung trend H1 (EMA50 slope + posisi vs EMA200)
    dan cari S/R zone dari 720 candle terakhir (~1 bulan).

    Returns dict berisi data H1 + _sup_zones + _res_zones (raw, untuk analyze_m15).
    """
    ema50  = calculate_ema(df_h1, 50)
    ema200 = calculate_ema(df_h1, 200)
    atr_h1 = calculate_atr(df_h1, 14)

    # Candle closed (-2), bukan candle berjalan (-1)
    e50        = float(ema50.iloc[-2])
    e200       = float(ema200.iloc[-2])
    atr_val_h1 = float(atr_h1.iloc[-2])
    slope      = float(ema50.iloc[-2]) - float(ema50.iloc[-5])
    close      = float(df_h1["close"].iloc[-2])

    # MACD H1 — konfirmasi momentum besar (dari candle closed)
    _, _, macd_hist_h1 = calculate_macd(df_h1)
    macd_h1_curr = float(macd_hist_h1.iloc[-2])
    macd_h1_prev = float(macd_hist_h1.iloc[-3])

    # Trend: slope wajib + salah satu (close vs EMA200 ATAU EMA50 vs EMA200)
    if slope > SLOPE_THRESHOLD and (close > e200 or e50 > e200):
        trend = "up"
    elif slope < -SLOPE_THRESHOLD and (close < e200 or e50 < e200):
        trend = "down"
    else:
        trend = "sideways"

    # S/R Zone dari candle closed (buang candle berjalan)
    res_levels, sup_levels = find_swing_levels(df_h1.iloc[:-1], lookback=720, window=4)
    res_zones = cluster_zones(res_levels)
    sup_zones = cluster_zones(sup_levels)

    # Cek proximity harga ke zona (1.5x ATR H1) — diperlebar agar tidak miss
    thr           = 1.5 * atr_val_h1
    in_resistance = any(abs(close - z) <= thr for z in res_zones)
    in_support    = any(abs(close - z) <= thr for z in sup_zones)

    return {
        "open_h1":       round(float(df_h1["open"].iloc[-2]), 4),
        "high_h1":       round(float(df_h1["high"].iloc[-2]), 4),
        "low_h1":        round(float(df_h1["low"].iloc[-2]), 4),
        "close_h1":      round(close, 4),
        "volume_h1":     round(float(df_h1["volume"].iloc[-2]), 2),
        "trend_h1":      trend,
        "ema_50_h1":     round(e50, 4),
        "ema_200_h1":    round(e200, 4),
        "in_support":    in_support,
        "in_resistance": in_resistance,
        "atr_h1":        round(atr_val_h1, 5),
        "macd_hist_h1":  round(macd_h1_curr, 6),
        "macd_h1_rising": macd_h1_curr > macd_h1_prev,
        "_sup_zones":    sup_zones,
        "_res_zones":    res_zones,
    }


def analyze_m15(df_m15: pd.DataFrame, sup_zones: list, res_zones: list) -> dict:
    """
    Cek 3 komponen entry M15 — SEMUA dihitung dari candle CLOSED (.iloc[-2]),
    bukan candle berjalan, supaya signal tidak berubah-ubah dalam 1 candle:
    1. MACD histogram arah
    2. EMA9 vs EMA21 posisi
    3. Candle pattern (pin bar / engulfing)
    """
    # Candle berjalan (-1) belum close → pakai candle closed (-2) untuk OHLC
    close = float(df_m15["close"].iloc[-2])
    o_val = float(df_m15["open"].iloc[-2])
    h_val = float(df_m15["high"].iloc[-2])
    l_val = float(df_m15["low"].iloc[-2])

    atr     = calculate_atr(df_m15, 14)
    atr_val = float(atr.iloc[-2])

    _, _, histogram = calculate_macd(df_m15)
    hist_curr = float(histogram.iloc[-2])   # candle closed terakhir
    hist_prev = float(histogram.iloc[-3])   # candle closed sebelumnya
    macd_up   = hist_curr > hist_prev and hist_curr > 0
    macd_down = hist_curr < hist_prev and hist_curr < 0

    ema9  = calculate_ema(df_m15, 9)
    ema21 = calculate_ema(df_m15, 21)
    e9    = float(ema9.iloc[-2])
    e21   = float(ema21.iloc[-2])

    # Candle pattern dari candle closed → buang candle berjalan dulu
    has_bull_pattern, has_bear_pattern = detect_candle_pattern(df_m15.iloc[:-1])

    thr_m15         = 1.5 * atr_val
    near_support    = any(abs(close - z) <= thr_m15 for z in sup_zones)
    near_resistance = any(abs(close - z) <= thr_m15 for z in res_zones)
    macd_slope      = round(hist_curr - hist_prev, 6)

    # Konfirmasi reaksi candle: bounce = candle closed berbalik searah trend
    bullish_close = close > o_val   # candle closed bullish (rejeksi support)
    bearish_close = close < o_val   # candle closed bearish (rejeksi resistance)

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
        "bullish_close":       bullish_close,
        "bearish_close":       bearish_close,
    }


def get_confluence_score(
    action: str,
    h1: dict,
    m15: dict,
    trend_h4: str = "sideways",
    trend_d1: str = "sideways",
) -> int:
    """
    Hitung confluence score (0-9) untuk arah buy atau sell.

    Blocker (langsung return 0):
      • Trend H1 tidak searah
      • H4 atau D1 berlawanan tegas
      • MACD H1 berlawanan tegas       ← momentum besar harus searah
      • Koreksi M15 masih aktif         ← MACD turun + candle lawan arah

    Poin (maks 9):
      +1  Trend H1 searah          ← wajib
      +1  H4 searah atau netral
      +1  D1 searah atau netral
      +1  Harga di S/R zone H1
      +1  MACD H1 searah           ← konfirmasi momentum besar
      +1  MACD M15 searah          ← berlawanan = -1 penalty
      +1  EMA9/21 M15 searah
      +1  Candle pattern konfirmasi
      +1  Bounce confirmed dari S/R ← candle closed berbalik di zona

    Minimum score 5 untuk signal masuk (lihat usecase).
    """
    trend      = h1["trend_h1"]
    in_sup     = h1["in_support"]
    in_res     = h1["in_resistance"]
    macd_m15   = m15["macd_histogram_m15"]
    macd_slope = m15.get("macd_slope", 0.0)
    macd_h1    = h1.get("macd_hist_h1", 0.0)
    bull_close = m15.get("bullish_close", False)
    bear_close = m15.get("bearish_close", False)

    score = 0

    if action == "buy":
        if trend != "up":       return 0   # H1 wajib
        if trend_h4 == "down":  return 0   # H4 blocker
        if trend_d1 == "down":  return 0   # D1 blocker
        if macd_h1 < 0:         return 0   # momentum H1 berlawanan = blocker
        # Koreksi M15 masih aktif: MACD turun DAN candle bearish → tunggu selesai
        if macd_slope < 0 and bear_close:  return 0

        score += 1                                      # H1 up
        if trend_h4 == "up":    score += 1             # H4 konfirmasi
        if trend_d1 == "up":    score += 1             # D1 konfirmasi
        if in_sup:              score += 1             # di support zone
        if macd_h1 > 0:         score += 1             # MACD H1 searah
        if macd_m15 > 0:        score += 1             # MACD M15 searah
        elif macd_m15 < 0:      score -= 1             # MACD M15 berlawanan → penalty
        if m15["ema_bias"] == "buy":   score += 1     # EMA9 > EMA21
        if m15["has_bull_pattern"]:    score += 1     # candle pattern
        # Bounce: di support DAN candle closed bullish (rejeksi, bukan breakdown)
        if in_sup and m15.get("bullish_close"):   score += 1

    elif action == "sell":
        if trend != "down":     return 0
        if trend_h4 == "up":    return 0
        if trend_d1 == "up":    return 0
        if macd_h1 > 0:         return 0   # momentum H1 berlawanan = blocker
        # Koreksi M15 masih aktif: MACD naik DAN candle bullish → tunggu selesai
        if macd_slope > 0 and bull_close:  return 0

        score += 1
        if trend_h4 == "down":  score += 1
        if trend_d1 == "down":  score += 1
        if in_res:              score += 1
        if macd_h1 < 0:         score += 1             # MACD H1 searah
        if macd_m15 < 0:        score += 1
        elif macd_m15 > 0:      score -= 1             # penalty
        if m15["ema_bias"] == "sell":  score += 1
        if m15["has_bear_pattern"]:    score += 1
        # Bounce: di resistance DAN candle closed bearish (rejeksi, bukan breakout)
        if in_res and m15.get("bearish_close"):   score += 1

    return max(0, score)
