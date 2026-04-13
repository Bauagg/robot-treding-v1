import httpx
import pandas as pd
import MetaTrader5 as mt5
from loguru import logger

from app.config.settings import settings
from app.utils.indicators import calculate_macd, calculate_atr, calculate_obv, find_swing_levels
from app.utils.analysis import cluster_zones


# Map symbol → nama field token di settings
# Key pakai prefix tanpa suffix broker (EURUSDm → EURUSD)
_SYMBOL_TOKEN_MAP = {
    "EURUSD": "TELEGRAM_TOKEN_EURUSD",
    "XAUUSD": "TELEGRAM_TOKEN_XAUUSD",
    "GBPUSD": "TELEGRAM_TOKEN_GBPUSD",
    "USDJPY": "TELEGRAM_TOKEN_USDJPY",
    "BTCUSD": "TELEGRAM_TOKEN_BTCUSD",
}


def _get_token(symbol: str) -> str:
    """Ambil token bot sesuai symbol. Strip suffix broker (m, c, dll)."""
    # Hapus suffix satu karakter di belakang kalau bukan digit (m, c, dll)
    s = symbol.upper()
    if s and not s[-1].isdigit() and s[-1].isalpha() and len(s) > 6:
        s = s[:-1]
    field = _SYMBOL_TOKEN_MAP.get(s, "")
    token = getattr(settings, field, "") if field else ""
    logger.debug(f"Telegram token lookup: {symbol} → key={s} field={field} token={'SET' if token else 'EMPTY'}")
    return token


async def send_telegram(message: str, symbol: str = "") -> None:
    """Kirim pesan ke Telegram. Token dipilih berdasarkan symbol."""
    token   = _get_token(symbol) if symbol else ""
    chat_id = settings.TELEGRAM_CHAT_ID

    if not token or not chat_id:
        logger.debug(f"Telegram token tidak dikonfigurasi untuk {symbol} — skip")
        return

    url  = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=data)
            if resp.status_code != 200:
                logger.warning(f"Telegram [{symbol}] gagal | status={resp.status_code} | {resp.text}")
    except Exception as e:
        logger.warning(f"Telegram [{symbol}] error: {e}")


# ─── Fetch candle untuk analisis ─────────────────────────────────────────────

def _fetch_df(symbol: str, tf_const, count: int) -> pd.DataFrame | None:
    rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, count)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.rename(columns={"tick_volume": "volume"}, inplace=True)
    return df


def fetch_all_symbols() -> dict[str, dict[str, pd.DataFrame]]:
    """
    Fetch D1/H4/H1/M15 untuk semua symbol di WATCH_SYMBOLS.
    Buka koneksi MT5 satu kali untuk semua symbol.
    Return: { "EURUSDm": {"D1": df, "H4": df, ...}, ... }
    """
    symbols = [s.strip() for s in settings.WATCH_SYMBOLS.split(",") if s.strip()]
    ok = mt5.initialize(
        path=settings.MT5_PATH,
        login=settings.MT5_LOGIN,
        password=settings.MT5_PASSWORD,
        server=settings.MT5_SERVER,
    )
    if not ok:
        return {}
    try:
        result = {}
        for sym in symbols:
            result[sym] = {
                "D1":  _fetch_df(sym, mt5.TIMEFRAME_D1,  365),   # 1 tahun — level mayor terbukti
                "H4":  _fetch_df(sym, mt5.TIMEFRAME_H4,  720),   # ~4 bulan — zona aktif cukup dalam
                "H1":  _fetch_df(sym, mt5.TIMEFRAME_H1,  720),   # ~1 bulan — struktur penuh
                "M15": _fetch_df(sym, mt5.TIMEFRAME_M15, 500),   # ~5 hari — swing M15 valid untuk entry
            }
        return result
    finally:
        mt5.shutdown()


def fetch_multi_tf(symbol: str) -> dict[str, pd.DataFrame] | None:
    """Fetch D1, H4, H1, M15 untuk satu symbol. Dipakai oleh monitor_pending_orders."""
    ok = mt5.initialize(
        path=settings.MT5_PATH,
        login=settings.MT5_LOGIN,
        password=settings.MT5_PASSWORD,
        server=settings.MT5_SERVER,
    )
    if not ok:
        return None
    try:
        return {
            "D1":  _fetch_df(symbol, mt5.TIMEFRAME_D1,  365),
            "H4":  _fetch_df(symbol, mt5.TIMEFRAME_H4,  720),
            "H1":  _fetch_df(symbol, mt5.TIMEFRAME_H1,  720),
            "M15": _fetch_df(symbol, mt5.TIMEFRAME_M15, 500),
        }
    finally:
        mt5.shutdown()


# ─── Analisis per timeframe ───────────────────────────────────────────────────

def _trend_label(df: pd.DataFrame) -> str:
    """
    Hitung trend dari struktur candlestick (price action):
    - Ambil 3 swing high dan 3 swing low terakhir
    - UP    : HH (higher high) + HL (higher low)
    - DOWN  : LH (lower high)  + LL (lower low)
    - Selain itu: SIDEWAYS
    """
    highs = df["high"].values
    lows  = df["low"].values
    n     = len(df)
    w     = 3   # window kiri & kanan untuk swing

    swing_highs = []
    swing_lows  = []

    for i in range(w, n - w):
        if highs[i] == max(highs[i - w: i + w + 1]):
            swing_highs.append(highs[i])
        if lows[i] == min(lows[i - w: i + w + 1]):
            swing_lows.append(lows[i])

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "SIDEWAYS"

    # Ambil 2 swing terakhir
    hh = swing_highs[-1] > swing_highs[-2]   # higher high
    hl = swing_lows[-1]  > swing_lows[-2]    # higher low
    lh = swing_highs[-1] < swing_highs[-2]   # lower high
    ll = swing_lows[-1]  < swing_lows[-2]    # lower low

    if hh and hl:
        return "UP"
    if lh and ll:
        return "DOWN"
    return "SIDEWAYS"


def _volume_detail(df: pd.DataFrame) -> dict:
    """
    Analisis volume pakai OBV + volume candle terakhir vs rata-rata.

    - OBV naik  = tekanan beli (akumulasi)
    - OBV turun = tekanan jual (distribusi)
    - Volume candle terakhir vs avg 20 candle → tinggi/normal/rendah
    - Prediksi arah: OBV + trend volume
    """
    try:
        obv     = calculate_obv(df)
        curr_obv = float(obv.iloc[-1])
        prev_obv = float(obv.iloc[-4])   # slope 4 candle
        obv_slope = curr_obv - prev_obv

        vol_curr = float(df["volume"].iloc[-1])
        vol_avg  = float(df["volume"].iloc[-20:].mean())
        vol_ratio = vol_curr / vol_avg if vol_avg > 0 else 1.0

        if vol_ratio >= 1.5:
            vol_label = f"🔥 TINGGI ({vol_ratio:.1f}x avg)"
        elif vol_ratio >= 0.8:
            vol_label = f"➡️ NORMAL ({vol_ratio:.1f}x avg)"
        else:
            vol_label = f"😴 RENDAH ({vol_ratio:.1f}x avg)"

        if obv_slope > 0 and vol_ratio >= 1.0:
            prediksi = "🟢 Tekanan BELI — potensi naik"
        elif obv_slope < 0 and vol_ratio >= 1.0:
            prediksi = "🔴 Tekanan JUAL — potensi turun"
        elif obv_slope > 0:
            prediksi = "🟡 Akumulasi lemah — hati-hati"
        elif obv_slope < 0:
            prediksi = "🟡 Distribusi lemah — hati-hati"
        else:
            prediksi = "⚪ Sideways — tidak ada tekanan"

        return {
            "vol_label": vol_label,
            "obv_slope": obv_slope,
            "prediksi":  prediksi,
        }
    except Exception:
        return {"vol_label": "?", "obv_slope": 0, "prediksi": "?"}


def _macd_detail(df: pd.DataFrame) -> dict:
    """Kalkulasi MACD lengkap — line, signal, histogram, arah."""
    try:
        macd_line, signal_line, histogram = calculate_macd(df)
        curr_hist = float(histogram.iloc[-1])
        prev_hist = float(histogram.iloc[-2])
        curr_macd = float(macd_line.iloc[-1])
        curr_sig  = float(signal_line.iloc[-1])

        if curr_hist > prev_hist and curr_hist > 0:
            arah = "⬆️ NAIK"
        elif curr_hist < prev_hist and curr_hist < 0:
            arah = "⬇️ TURUN"
        elif curr_hist > 0:
            arah = "↗️ Bullish melemah"
        elif curr_hist < 0:
            arah = "↘️ Bearish melemah"
        else:
            arah = "↔️ FLAT"

        return {
            "arah":      arah,
            "macd":      curr_macd,
            "signal":    curr_sig,
            "histogram": curr_hist,
            "slope":     round(curr_hist - prev_hist, 6),
        }
    except Exception:
        return {"arah": "?", "macd": 0, "signal": 0, "histogram": 0, "slope": 0}


def _sr_zones(df: pd.DataFrame, close: float) -> dict:
    """
    Ambil semua zona S/R, tandai yang dekat harga sekarang.
    Return dict: sup_zones, res_zones, near_sup, near_res
    """
    try:
        atr_val   = float(calculate_atr(df, 14).iloc[-1])
        res_raw, sup_raw = find_swing_levels(df, lookback=min(500, len(df) - 10), window=4)
        res_zones = sorted(cluster_zones(res_raw))
        sup_zones = sorted(cluster_zones(sup_raw))
        thr       = 1.0 * atr_val
        near_sup  = [z for z in sup_zones if abs(close - z) <= thr]
        near_res  = [z for z in res_zones if abs(close - z) <= thr]
        return {
            "sup_zones": sup_zones,
            "res_zones": res_zones,
            "near_sup":  near_sup,
            "near_res":  near_res,
        }
    except Exception:
        return {"sup_zones": [], "res_zones": [], "near_sup": [], "near_res": []}


# ─── Build pesan analisis ─────────────────────────────────────────────────────

def build_market_analysis(symbol: str, frames: dict[str, pd.DataFrame]) -> str:
    from datetime import datetime
    now = datetime.now().strftime("%d/%m %H:%M")
    L   = []

    # ── Header ──────────────────────────────
    L.append(f"<b>📊 {symbol} | {now}</b>")
    L.append("─" * 24)

    # ── Trend ───────────────────────────────
    _TF_ICON = {"UP": "📈", "DOWN": "📉", "SIDEWAYS": "➡️"}
    trends = {}
    trend_lines = []
    for tf in ("D1", "H4", "H1", "M15"):
        df = frames.get(tf)
        if df is None or len(df) < 50:
            continue
        t = _trend_label(df)
        trends[tf] = t
        trend_lines.append(f"{tf} {_TF_ICON.get(t, '')} {t}")
    L.append("  ".join(trend_lines))

    # ── MACD ────────────────────────────────
    L.append("")
    L.append("<b>MACD</b>")
    for tf in ("D1", "H4", "H1", "M15"):
        df = frames.get(tf)
        if df is None or len(df) < 35:
            continue
        m   = _macd_detail(df)
        pos = "bullish" if m["histogram"] > 0 else "bearish"
        spd = "kuat" if abs(m["slope"]) > 0.00005 else ("pelan" if abs(m["slope"]) > 0.00001 else "flat")
        arah_mv = "naik" if m["slope"] > 0 else ("turun" if m["slope"] < 0 else "")
        L.append(f"  {tf} {m['arah']} — {pos}, {spd} {arah_mv}".strip())

    # ── Volume ──────────────────────────────
    L.append("")
    L.append("<b>Volume</b>")
    for tf in ("D1", "H4", "H1", "M15"):
        df = frames.get(tf)
        if df is None or len(df) < 25:
            continue
        v = _volume_detail(df)
        L.append(f"  {tf} {v['vol_label']} → {v['prediksi']}")

    # ── ATR ─────────────────────────────────
    df_m15 = frames.get("M15")
    if df_m15 is not None and len(df_m15) >= 15:
        atr = float(calculate_atr(df_m15, 14).iloc[-1])
        pip = round(atr / 0.0001, 1)
        L.append(f"\n<b>Volatilitas</b> ~{pip} pip per candle M15")

    # ── S/R ─────────────────────────────────
    # S = merah (bearish zone / harga bisa turun ke sini)
    # R = hijau (bullish zone / harga bisa naik ke sini)
    L.append("")
    L.append("<b>Zona Harga</b>  <i>(⚠️ = harga sedang dekat)</i>")
    for tf in ("D1", "H4", "H1", "M15"):
        df = frames.get(tf)
        if df is None or len(df) < 20:
            continue
        close = float(df["close"].iloc[-1])
        sr    = _sr_zones(df, close)

        res_above = sorted([z for z in sr["res_zones"] if z > close])[:3]
        sup_below = sorted([z for z in sr["sup_zones"] if z < close], reverse=True)[:3]
        pairs     = list(zip(sup_below, res_above))

        if not pairs:
            continue

        labels = ["Terdekat", "Tengah", "Paling jauh"]
        L.append(f"  <b>{tf}</b> — harga {close:.5f}")
        for idx, (s, r) in enumerate(pairs):
            rng   = round((r - s) / 0.0001, 1)
            s_pip = round((close - s) / 0.0001, 1)
            r_pip = round((r - close) / 0.0001, 1)
            s_tag = "⚠️" if s in sr["near_sup"] else ""
            r_tag = "⚠️" if r in sr["near_res"] else ""
            lbl   = labels[idx] if idx < len(labels) else ""
            L.append(
                f"    [{lbl}] ↕{rng}p\n"
                f"    🔴 {s:.5f}{s_tag} (-{s_pip}p)  →  🟢 {r:.5f}{r_tag} (+{r_pip}p)"
            )

    # ── Kesimpulan ──────────────────────────
    L.append("")
    L.append("─" * 24)
    up   = sum(1 for t in trends.values() if t == "UP")
    down = sum(1 for t in trends.values() if t == "DOWN")
    n    = len(trends)

    if up >= 3:
        bias  = "🟢 BUY"
        saran = f"Cari entry BUY di zona 🔴 Support terdekat"
    elif down >= 3:
        bias  = "🔴 SELL"
        saran = f"Cari entry SELL di zona 🟢 Resistance terdekat"
    elif up > down:
        bias  = "🟡 Cenderung BUY"
        saran = "Tunggu konfirmasi dulu"
    elif down > up:
        bias  = "🟡 Cenderung SELL"
        saran = "Tunggu konfirmasi dulu"
    else:
        bias  = "⚪ Sideways"
        saran = "Tidak ada arah jelas, wait and see"

    L.append(f"<b>🎯 {bias}</b>  {up}↑ {down}↓ dari {n} TF")
    L.append(f"  {saran}")

    return "\n".join(L)
