import httpx
import pandas as pd
import MetaTrader5 as mt5
from loguru import logger

from app.config.settings import settings
from app.utils.indicators import calculate_macd, calculate_atr, find_swing_levels
from app.utils.analysis import cluster_zones


# Map symbol → nama field token di settings
# Key pakai prefix tanpa suffix broker (XAUUSDm → XAUUSD)
_SYMBOL_TOKEN_MAP = {
    "XAUUSD": "TELEGRAM_TOKEN_XAUUSD",
    "GBPUSD": "TELEGRAM_TOKEN_GBPUSD",
    "USDJPY": "TELEGRAM_TOKEN_USDJPY",
    "USDCAD": "TELEGRAM_TOKEN_USDCAD",
    "AUDUSD": "TELEGRAM_TOKEN_AUDUSD",
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


def fetch_all_symbols() -> dict[str, dict]:
    """
    Fetch D1/H4/H1/M15 dan harga live (bid) untuk semua symbol di WATCH_SYMBOLS.
    Buka koneksi MT5 satu kali untuk semua symbol.
    Return: { "XAUUSDm": {"D1": df, "H4": df, ..., "live_price": 2345.67}, ... }
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
            tick = mt5.symbol_info_tick(sym)
            live_price = float(tick.bid) if tick else None
            result[sym] = {
                "D1":  _fetch_df(sym, mt5.TIMEFRAME_D1,  365),
                "H4":  _fetch_df(sym, mt5.TIMEFRAME_H4,  720),
                "H1":  _fetch_df(sym, mt5.TIMEFRAME_H1,  720),
                "M15": _fetch_df(sym, mt5.TIMEFRAME_M15, 500),
                "live_price": live_price,
            }
        return result
    finally:
        mt5.shutdown()



# ─── Analisis per timeframe ───────────────────────────────────────────────────

def _trend_label(df: pd.DataFrame) -> str:
    """
    Hitung trend dari struktur swing — tahan terhadap pullback normal.

    Pakai 3 swing terakhir (bukan 2) supaya satu pullback tidak langsung
    membalik label. Tren tetap UP selama mayoritas struktur masih HH+HL,
    meski swing terakhir sedang koreksi.

    UP   : minimal 2 dari 3 pasang swing masih HH+HL
           AND harga saat ini belum break swing low ke-2 (masih pullback wajar)
    DOWN : minimal 2 dari 3 pasang swing masih LH+LL
           AND harga saat ini belum break swing high ke-2
    Selain itu: SIDEWAYS
    """
    highs = df["high"].values
    lows  = df["low"].values
    close = float(df["close"].iloc[-1])
    n     = len(df)
    w     = 3   # window lebih lebar → swing lebih signifikan, tidak mudah goyah

    swing_highs = []
    swing_lows  = []

    for i in range(w, n - w):
        if highs[i] == max(highs[i - w: i + w + 1]):
            swing_highs.append(highs[i])
        if lows[i] == min(lows[i - w: i + w + 1]):
            swing_lows.append(lows[i])

    if len(swing_highs) < 3 or len(swing_lows) < 3:
        return "SIDEWAYS"

    sh = swing_highs[-3:]   # 3 swing high terakhir
    sl = swing_lows[-3:]    # 3 swing low terakhir

    # Hitung berapa pasang yang HH/HL atau LH/LL
    hh_count = sum(1 for i in range(1, len(sh)) if sh[i] > sh[i - 1])
    hl_count = sum(1 for i in range(1, len(sl)) if sl[i] > sl[i - 1])
    lh_count = sum(1 for i in range(1, len(sh)) if sh[i] < sh[i - 1])
    ll_count = sum(1 for i in range(1, len(sl)) if sl[i] < sl[i - 1])

    # Tren UP: mayoritas struktur masih naik + harga belum break swing low ke-2
    # (break swing low ke-2 = reversal, bukan pullback biasa)
    if hh_count >= 1 and hl_count >= 1 and close > sl[-2]:
        return "UP"

    # Tren DOWN: mayoritas struktur masih turun + harga belum break swing high ke-2
    if lh_count >= 1 and ll_count >= 1 and close < sh[-2]:
        return "DOWN"

    return "SIDEWAYS"


def _setup_quality(
    trends: dict,
    macd_frames: dict,
    sr_strong: bool,
) -> tuple[str, float, float]:
    """
    Tentukan kualitas setup berdasarkan:
    - Jumlah TF searah (dari trends dict)
    - MACD kuat (D1/H4 histogram naik kuat)
    - Harga di S/R kuat (D1 atau H4)

    Return (label, sl_mult, tp_mult)
    """
    tf_count = sum(1 for t in trends.values() if t in ("UP", "DOWN"))
    aligned  = len(set(trends.values()) - {"SIDEWAYS"}) <= 1  # semua searah

    # Cek MACD kuat di D1 atau H4
    macd_kuat = False
    for tf in ("D1", "H4"):
        m = macd_frames.get(tf)
        if m and abs(m["slope"]) > 0.00005 and (
            (m["histogram"] > 0 and m["slope"] > 0) or
            (m["histogram"] < 0 and m["slope"] < 0)
        ):
            macd_kuat = True
            break

    # ── Tentukan kualitas ──
    if aligned and tf_count >= 4 and macd_kuat and sr_strong:
        return "🔥 SETUP KUAT", 1.0, 2.5

    if tf_count >= 3 and (macd_kuat or sr_strong):
        return "✅ SETUP BAGUS", 1.2, 2.0

    return "⚠️ SETUP LEMAH", 1.5, 1.5


def _pip_size(symbol: str) -> float:
    """
    Ukuran 1 pip per symbol.
    Forex 4-digit  : 0.0001  (EURUSD, GBPUSD, dll)
    JPY pairs      : 0.01
    XAUUSD (Gold)  : 0.01    (harga 4 digit, 1 pip = $0.01)
    XAGUSD (Silver): 0.001
    Crypto BTC     : 1.0
    Crypto ETH/XRP : 0.1
    """
    s = symbol.upper()
    if "JPY" in s:
        return 0.01
    if "XAU" in s:
        return 0.01
    if "XAG" in s:
        return 0.001
    if "BTC" in s:
        return 1.0
    if any(x in s for x in ("ETH", "XRP", "LTC", "BNB")):
        return 0.1
    return 0.0001


def _price_decimals(symbol: str) -> int:
    """Jumlah desimal untuk format harga."""
    s = symbol.upper()
    if "BTC" in s:
        return 2
    if any(x in s for x in ("ETH", "XAU", "XAG")):
        return 2
    if "JPY" in s:
        return 3
    return 5


def _order_calc(
    close: float,
    bias: str,
    atr: float,
    sl_mult: float,
    tp_mult: float,
    symbol: str = "",
) -> dict:
    """Hitung SL dan TP berdasarkan multiplier kualitas setup."""
    pip     = _pip_size(symbol)
    dec     = _price_decimals(symbol)
    sl_dist = atr * sl_mult
    tp_dist = atr * tp_mult
    sl_pip  = round(sl_dist / pip, 1)
    tp_pip  = round(tp_dist / pip, 1)
    rr      = round(tp_pip / sl_pip, 2) if sl_pip > 0 else 0

    if bias == "BUY":
        sl = round(close - sl_dist, dec)
        tp = round(close + tp_dist, dec)
    else:
        sl = round(close + sl_dist, dec)
        tp = round(close - tp_dist, dec)

    return {"sl": sl, "tp": tp, "sl_pip": sl_pip, "tp_pip": tp_pip, "rr": rr, "dec": dec}


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


# ─── Deteksi fase: trending atau koreksi ─────────────────────────────────────

def _detect_phase(df_h1: pd.DataFrame, df_m15: pd.DataFrame, trend: str, pip: float = 0.0001, live_price: float | None = None) -> dict:
    """
    Deteksi apakah harga sedang TRENDING (lanjut) atau RETRACING (koreksi).
    Pakai MACD histogram H1 + M15 dan posisi harga vs swing low/high terdekat.

    Return:
      phase  : "TRENDING" | "RETRACING" | "UNCLEAR"
      label  : emoji + teks untuk ditampilkan
      note   : penjelasan singkat kenapa
      entry  : (opsional) level harga untuk masuk saat koreksi selesai
    """
    if trend not in ("UP", "DOWN"):
        return {"phase": "UNCLEAR", "label": "➡️ Sideways", "note": "Tidak ada tren jelas"}

    try:
        # ── MACD histogram H1 ──
        _, _, hist_h1 = calculate_macd(df_h1)
        h1_hist = float(hist_h1.iloc[-1])
        h1_prev = float(hist_h1.iloc[-2])

        # ── MACD histogram M15 ──
        _, _, hist_m15 = calculate_macd(df_m15)
        m15_hist = float(hist_m15.iloc[-1])
        m15_prev = float(hist_m15.iloc[-2])

        # ── Posisi harga vs swing low/high H1 ──
        # Pakai harga live kalau ada supaya jarak ke swing akurat ke kondisi sekarang.
        close    = live_price if live_price else float(df_m15["close"].iloc[-1])
        res_raw, sup_raw = find_swing_levels(df_h1, lookback=min(200, len(df_h1) - 10), window=3)

        # Swing support terdekat di bawah harga (untuk UP trend)
        sup_below = sorted([z for z in sup_raw if z < close], reverse=True)
        near_sup  = sup_below[0] if sup_below else None

        # Swing resistance terdekat di atas harga (untuk DOWN trend)
        res_above = sorted([z for z in res_raw if z > close])
        near_res  = res_above[0] if res_above else None

    except Exception:
        return {"phase": "UNCLEAR", "label": "❓ Tidak bisa hitung", "note": ""}

    if trend == "UP":
        # MACD H1 masih positif dan naik = momentum lanjut
        h1_bullish  = h1_hist > 0 and h1_hist >= h1_prev
        # MACD M15 mulai naik = entry momentum muncul
        m15_bullish = m15_hist > m15_prev
        if h1_bullish and m15_bullish:
            return {"phase": "TRENDING", "label": "🚀 Lanjut naik", "note": "MACD H1+M15 bullish, momentum kuat"}

        # H1 masih positif tapi M15 mulai turun = koreksi M15 dalam tren H1
        if h1_hist > 0 and m15_hist < m15_prev:
            if near_sup:
                dist_pip = round((close - near_sup) / pip, 0)
                return {
                    "phase": "RETRACING",
                    "label": "🔄 Koreksi — tunggu support",
                    "note":  f"M15 melemah, support H1 terdekat ~{dist_pip:.0f} pip di bawah",
                    "entry": near_sup,   # BUY saat harga retrace ke support ini
                }
            return {"phase": "RETRACING", "label": "🔄 Koreksi", "note": "M15 melemah, MACD H1 masih positif"}

        # H1 histogram mulai negatif = koreksi lebih dalam
        if h1_hist < 0:
            return {"phase": "RETRACING", "label": "⚠️ Koreksi dalam", "note": "MACD H1 negatif — tunggu reversal M15"}

    else:  # DOWN
        h1_bearish  = h1_hist < 0 and h1_hist <= h1_prev
        m15_bearish = m15_hist < m15_prev

        if h1_bearish and m15_bearish:
            return {"phase": "TRENDING", "label": "🔻 Lanjut turun", "note": "MACD H1+M15 bearish, momentum kuat"}

        if h1_hist < 0 and m15_hist > m15_prev:
            if near_res:
                dist_pip = round((near_res - close) / pip, 0)
                return {
                    "phase": "RETRACING",
                    "label": "🔄 Koreksi — tunggu resistance",
                    "note":  f"M15 menguat, resistance H1 terdekat ~{dist_pip:.0f} pip di atas",
                    "entry": near_res,   # SELL saat harga retrace ke resistance ini
                }
            return {"phase": "RETRACING", "label": "🔄 Koreksi", "note": "M15 menguat, MACD H1 masih negatif"}

        if h1_hist > 0:
            return {"phase": "RETRACING", "label": "⚠️ Koreksi dalam", "note": "MACD H1 positif — tunggu reversal M15"}

    return {"phase": "UNCLEAR", "label": "❓ Tidak jelas", "note": "Momentum mixed"}


# ─── Build pesan analisis ─────────────────────────────────────────────────────

def build_market_analysis(symbol: str, frames: dict) -> str:
    from datetime import datetime
    now = datetime.now().strftime("%d/%m %H:%M")
    L   = []

    # Ambil harga live sebelum frames di-strip
    live_price: float | None = frames.pop("live_price", None)

    # ── Buang candle berjalan (belum close) dari semua TF ──
    # Analisa harus pakai candle yang sudah selesai supaya tidak kedip-kedip.
    frames = {
        tf: (df.iloc[:-1] if df is not None and len(df) > 1 else df)
        for tf, df in frames.items()
    }

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

    # ── ATR + Kalkulasi Order ────────────────
    df_m15 = frames.get("M15")
    if df_m15 is not None and len(df_m15) >= 15:
        atr   = float(calculate_atr(df_m15, 14).iloc[-1])
        pip   = round(atr / _pip_size(symbol), 1)
        close = float(df_m15["close"].iloc[-1])
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
        # M15 = posisi harga relatif ke harga live sekarang;
        # TF besar pakai close candle masing-masing (tidak bergerak secepat M15).
        # Zona S/R tetap dihitung dari struktur candle closed (analisa strategis).
        close = float(df["close"].iloc[-1])
        ref   = live_price if (tf == "M15" and live_price) else close
        sr    = _sr_zones(df, ref)

        res_above = sorted([z for z in sr["res_zones"] if z > ref])[:3]
        sup_below = sorted([z for z in sr["sup_zones"] if z < ref], reverse=True)[:3]
        pairs     = list(zip(sup_below, res_above))

        if not pairs:
            continue

        labels = ["Terdekat", "Tengah", "Paling jauh"]
        pip    = _pip_size(symbol)
        dec    = _price_decimals(symbol)
        L.append(f"  <b>{tf}</b> — harga {ref:.{dec}f}")
        for idx, (s, r) in enumerate(pairs):
            rng   = round((r - s) / pip, 1)
            s_pip = round((ref - s) / pip, 1)
            r_pip = round((r - ref) / pip, 1)
            s_tag = "⚠️" if s in sr["near_sup"] else ""
            r_tag = "⚠️" if r in sr["near_res"] else ""
            lbl   = labels[idx] if idx < len(labels) else ""
            L.append(
                f"    [{lbl}] ↕{rng}p\n"
                f"    🔴 {s:.{dec}f}{s_tag} (-{s_pip}p)  →  🟢 {r:.{dec}f}{r_tag} (+{r_pip}p)"
            )

    # ── Kesimpulan ──────────────────────────
    L.append("")
    L.append("─" * 24)
    up   = sum(1 for t in trends.values() if t == "UP")
    down = sum(1 for t in trends.values() if t == "DOWN")
    n    = len(trends)

    if up >= 4:
        bias  = "🟢 BUY"
        saran = "Cari entry BUY di zona 🔴 Support terdekat"
    elif down >= 4:
        bias  = "🔴 SELL"
        saran = "Cari entry SELL di zona 🟢 Resistance terdekat"
    else:
        return ""   # TF tidak cukup searah — tidak kirim

    L.append(f"<b>🎯 {bias}</b>  {up}↑ {down}↓ dari {n} TF")
    L.append(f"  {saran}")

    # ── Fase pasar (koreksi atau lanjut) ────────
    df_h1_phase  = frames.get("H1")
    df_m15_phase = frames.get("M15")
    trend_for_phase = "UP" if up >= down else "DOWN"
    if df_h1_phase is not None and df_m15_phase is not None and len(df_h1_phase) >= 35 and len(df_m15_phase) >= 35:
        phase = _detect_phase(df_h1_phase, df_m15_phase, trend_for_phase, pip=_pip_size(symbol), live_price=live_price)
        L.append(f"\n<b>Fase</b>  {phase['label']}")
        if phase["note"]:
            L.append(f"  <i>{phase['note']}</i>")
        if phase["phase"] == "RETRACING":
            L.append("  ⏳ <b>Tunggu koreksi selesai sebelum entry</b>")

            # Kalau ada level retrace yang jelas, kasih angka entry/SL/TP-nya
            # supaya user tahu harus pasang pending di harga berapa.
            entry_lvl = phase.get("entry")
            df_m15_e  = frames.get("M15")
            if entry_lvl and df_m15_e is not None and len(df_m15_e) >= 15:
                atr_e  = float(calculate_atr(df_m15_e, 14).iloc[-1])
                side_e = "BUY" if trend_for_phase == "UP" else "SELL"
                # SL/TP dari level entry retrace, multiplier setup bagus (1.2 / 2.0)
                oe  = _order_calc(entry_lvl, side_e, atr_e, 1.2, 2.0, symbol)
                dec = oe["dec"]
                L.append(f"\n  <b>📌 Rencana {side_e} (pending di koreksi)</b>")
                L.append(f"    Entry : {entry_lvl:.{dec}f}")
                L.append(f"    SL    : {oe['sl']:.{dec}f}  (-{oe['sl_pip']:.0f} pip)")
                L.append(f"    TP    : {oe['tp']:.{dec}f}  (+{oe['tp_pip']:.0f} pip)")
                L.append(f"    R:R   : 1 : {oe['rr']}")

    # ── Kalkulasi Order ──────────────────────
    has_bias = bias not in ("⚪ Sideways",)
    if df_m15 is not None and len(df_m15) >= 15 and has_bias:
        atr   = float(calculate_atr(df_m15, 14).iloc[-1])
        close = live_price if live_price else float(df_m15["close"].iloc[-1])
        side  = "BUY" if up >= down else "SELL"

        # Kumpulkan MACD detail semua TF untuk quality check
        macd_frames = {}
        for tf in ("D1", "H4"):
            df = frames.get(tf)
            if df is not None and len(df) >= 35:
                _, _, histogram = calculate_macd(df)
                hist_curr = float(histogram.iloc[-1])
                hist_prev = float(histogram.iloc[-2])
                macd_frames[tf] = {
                    "histogram": hist_curr,
                    "slope":     round(hist_curr - hist_prev, 6),
                }

        # Cek apakah harga di S/R kuat (D1 atau H4)
        sr_strong = False
        for tf in ("D1", "H4"):
            df = frames.get(tf)
            if df is not None and len(df) >= 20:
                c  = float(df["close"].iloc[-1])
                sr = _sr_zones(df, c)
                if sr["near_sup"] or sr["near_res"]:
                    sr_strong = True
                    break

        # ── Hitung signal score (0-6) ──────────────
        # +1 H1 trend searah
        # +1 H4 searah atau netral (sideways ok)
        # +1 D1 searah atau netral (sideways ok)
        # +1 MACD M15 histogram searah
        # +1 MACD H1 histogram searah
        # +1 Harga di S/R kuat
        # H4 berlawanan tegas → langsung 0 (blocker)
        # M15 MACD berlawanan → -1 (penalty momentum entry)
        score = 0
        h1_trend = trends.get("H1", "SIDEWAYS")
        h4_trend = trends.get("H4", "SIDEWAYS")
        d1_trend = trends.get("D1", "SIDEWAYS")

        # MACD M15 dan H1
        macd_m15_hist = None
        df_m15_check = frames.get("M15")
        if df_m15_check is not None and len(df_m15_check) >= 35:
            _, _, hist = calculate_macd(df_m15_check)
            macd_m15_hist = float(hist.iloc[-1])

        macd_h1_hist = None
        df_h1_check = frames.get("H1")
        if df_h1_check is not None and len(df_h1_check) >= 35:
            _, _, hist = calculate_macd(df_h1_check)
            macd_h1_hist = float(hist.iloc[-1])

        if side == "BUY":
            if h4_trend == "DOWN":                           # blocker keras
                score = 0
            else:
                if h1_trend == "UP":                         score += 1
                if h4_trend in ("UP", "SIDEWAYS"):           score += 1
                if d1_trend in ("UP", "SIDEWAYS"):           score += 1
                if macd_m15_hist is not None:
                    if macd_m15_hist > 0:                    score += 1
                    elif macd_m15_hist < 0:                  score -= 1   # penalty: momentum entry berlawanan
                if macd_h1_hist is not None and macd_h1_hist > 0: score += 1
                if sr_strong:                                score += 1
        else:  # SELL
            if h4_trend == "UP":                             # blocker keras
                score = 0
            else:
                if h1_trend == "DOWN":                       score += 1
                if h4_trend in ("DOWN", "SIDEWAYS"):         score += 1
                if d1_trend in ("DOWN", "SIDEWAYS"):         score += 1
                if macd_m15_hist is not None:
                    if macd_m15_hist < 0:                    score += 1
                    elif macd_m15_hist > 0:                  score -= 1   # penalty: momentum entry berlawanan
                if macd_h1_hist is not None and macd_h1_hist < 0: score += 1
                if sr_strong:                                score += 1

        score = max(0, score)   # tidak boleh negatif

        # Score < 4 → tidak kirim pesan sama sekali
        if score < 4:
            return ""

        qlabel, sl_mult, tp_mult = _setup_quality(trends, macd_frames, sr_strong)
        o = _order_calc(close, side, atr, sl_mult, tp_mult, symbol)

        # Bar kekuatan visual
        filled = "█" * score
        empty  = "░" * (6 - score)
        bar    = f"{filled}{empty} {score}/6"

        L.append("")
        dec = o["dec"]
        L.append(f"<b>📌 Kalkulasi {side}</b>  {qlabel}")
        L.append(f"  Kekuatan : {bar}")
        L.append(f"  Entry    : {close:.{dec}f}")
        L.append(f"  SL       : {o['sl']:.{dec}f}  (-{o['sl_pip']:.0f} pip)")
        L.append(f"  TP       : {o['tp']:.{dec}f}  (+{o['tp_pip']:.0f} pip)")
        L.append(f"  R:R      : 1 : {o['rr']}")

    return "\n".join(L)
