import asyncio
from datetime import date, timezone, datetime
import MetaTrader5 as mt5
import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.utils.indicators import (
    calculate_ema,
    calculate_macd,
    calculate_atr,
    find_swing_levels,
    detect_candle_pattern,
    classify_candle,
)
from app.modules.candle_pattern.repository import CandlePatternRepository
from app.modules.trade_signal.repository import TradeSignalRepository
from app.modules.trade_order.usecase import TradeOrderUsecase


# Mapping string timeframe → konstanta MT5
_TF_MAP = {
    "1m":  mt5.TIMEFRAME_M1,
    "5m":  mt5.TIMEFRAME_M5,
    "15m": mt5.TIMEFRAME_M15,
    "30m": mt5.TIMEFRAME_M30,
    "1h":  mt5.TIMEFRAME_H1,
    "4h":  mt5.TIMEFRAME_H4,
    "1d":  mt5.TIMEFRAME_D1,
}

# ─── Jam trading terbaik (UTC) ─────────────────────────────────────────────
# Tidak ada filter jam — bot jalan 24 jam
# Signal tetap difilter oleh confluence score >= 3
BEST_HOURS_UTC = set(range(24))

# ─── Trend filter threshold ────────────────────────────────────────────────
# EMA50 slope minimal X pip dalam 3 candle H1
# Dari sweep backtest: 0.00025 = optimal (WR 50%, PF 1.445)
SLOPE_THRESHOLD = 0.00025


class TradeSignalUsecase:
    """
    Precision Strategy — Backtest 2021-2026 (Notebook 10)
    ──────────────────────────────────────────────────────
    Target : Modal $100 → profit $5-10/hari | Stop loss harian $5

    H1 (500 candle) → Trend filter + S/R Zone
      • EMA50 slope + harga vs EMA200 → trend UP/DOWN/SIDEWAYS
      • Swing high/low H1 dikumpulkan → zona Resistance/Support

    M15 → Entry timing (3 komponen):
      • MACD histogram naik (buy) / turun (sell)
      • EMA9 vs EMA21 posisi searah signal
      • Candle pattern: pin bar atau engulfing (bonus)

    Signal Score (0–5):
      +2 Trend H1 KUAT + harga di S/R Zone  ← dasar wajib
      +1 MACD histogram arah searah
      +1 EMA9/21 M15 posisi searah
      +1 Candle pattern (pin bar / engulfing)
      Signal masuk kalau score >= 3

    Jam trading : 02,03,08,09,10,12,13,16,17 UTC (WR >= 50%)
    ATR filter  : minimal 8 pips (0.0008)
    SL          : 1.0 × ATR M15
    TP1         : 1.5 × ATR M15  (RR 1:1.5)
    TP2         : 2.0 × ATR M15  (RR 1:2)
    Lot         : 0.02 (1 pip = $0.20)

    Hasil backtest (Des 2021 – Mar 2026):
      Win Rate      : 53.2%  (hanya jam terbaik)
      Profit Factor : 1.86
      Total USD     : +$395  (modal $100, lot 0.02)
      Max Drawdown  : $31.82
    """

    def __init__(self, symbol: str | None = None):
        self.symbol = symbol or settings.TRADING_SYMBOL

    # ─── Fetch Candle ──────────────────────────────────────────────────────

    def _fetch_candles(self, timeframe: str, count: int) -> pd.DataFrame:
        tf = _TF_MAP.get(timeframe)
        if tf is None:
            raise ValueError(f"Timeframe '{timeframe}' tidak dikenali.")

        rates = mt5.copy_rates_from_pos(self.symbol, tf, 0, count)
        if rates is None or len(rates) == 0:
            raise RuntimeError(
                f"Gagal fetch candle {self.symbol} {timeframe}: {mt5.last_error()}"
            )

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df.rename(columns={"tick_volume": "volume"}, inplace=True)
        logger.debug(f"Fetched {len(df)} candles | {self.symbol} {timeframe} | last: {df['time'].iloc[-1]}")
        return df

    def _fetch_all_candles(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        ok = mt5.initialize(
            path=settings.MT5_PATH,
            login=settings.MT5_LOGIN,
            password=settings.MT5_PASSWORD,
            server=settings.MT5_SERVER,
        )
        if not ok:
            raise RuntimeError(f"MT5 initialize gagal: {mt5.last_error()}")
        try:
            df_h1  = self._fetch_candles("1h",  count=520)   # 500 + buffer
            df_m15 = self._fetch_candles("15m", count=100)
            return df_h1, df_m15
        finally:
            mt5.shutdown()

    # ─── H1: Trend + S/R Zone ─────────────────────────────────────────────

    def _analyze_h1(self, df_h1: pd.DataFrame) -> dict:
        """
        Hitung trend H1 (EMA50 slope + posisi vs EMA200)
        dan cari S/R zone dari 500 candle terakhir.
        """
        ema50  = calculate_ema(df_h1, 50)
        ema200 = calculate_ema(df_h1, 200)
        atr_h1 = calculate_atr(df_h1, 14)

        e50  = float(ema50.iloc[-1])
        e200 = float(ema200.iloc[-1])
        atr_val_h1 = float(atr_h1.iloc[-1])
        slope = float(ema50.iloc[-1]) - float(ema50.iloc[-4])  # slope 3 candle
        close = float(df_h1["close"].iloc[-1])

        # Trend kuat: slope melewati threshold + posisi EMA50 vs EMA200 + harga searah
        # Dari sweep backtest: SLOPE_THRESHOLD=0.00025 = WR 50%, PF 1.445
        if slope > SLOPE_THRESHOLD and close > e200 and e50 > e200:
            trend = "up"
        elif slope < -SLOPE_THRESHOLD and close < e200 and e50 < e200:
            trend = "down"
        else:
            trend = "sideways"

        # S/R Zone dari 500 candle H1 terakhir (swing window=4)
        res_levels, sup_levels = find_swing_levels(df_h1, lookback=500, window=4)

        # Cluster levels dalam 10 pip
        def cluster(levels: list[float]) -> list[float]:
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

        res_zones = cluster(res_levels)
        sup_zones = cluster(sup_levels)

        # Cek apakah harga M15 saat ini di dekat zona (1.0x ATR H1)
        thr = 1.0 * atr_val_h1
        c_m15 = close  # pakai close H1 terakhir sebagai proxy

        in_resistance = any(abs(c_m15 - z) <= thr for z in res_zones)
        in_support    = any(abs(c_m15 - z) <= thr for z in sup_zones)

        return {
            "open_h1":     round(float(df_h1["open"].iloc[-1]), 4),
            "high_h1":     round(float(df_h1["high"].iloc[-1]), 4),
            "low_h1":      round(float(df_h1["low"].iloc[-1]), 4),
            "close_h1":    round(close, 4),
            "volume_h1":   round(float(df_h1["volume"].iloc[-1]), 2),
            "trend_h1":    trend,
            "ema_50_h1":   round(e50, 4),
            "ema_200_h1":  round(e200, 4),
            # S/R info (untuk log & DB)
            "in_support":    in_support,
            "in_resistance": in_resistance,
            "atr_h1":        round(atr_val_h1, 5),
            # raw zones untuk dipakai di get_signal
            "_sup_zones": sup_zones,
            "_res_zones": res_zones,
        }

    # ─── M15: Entry Trigger ────────────────────────────────────────────────

    def _analyze_m15(self, df_m15: pd.DataFrame, sup_zones: list, res_zones: list) -> dict:
        """
        Cek 3 komponen entry M15:
        1. MACD histogram arah
        2. EMA9 vs EMA21 posisi
        3. Candle pattern (pin bar / engulfing)
        + proximity ke S/R zone yang lebih presisi dari M15 close
        """
        close = float(df_m15["close"].iloc[-1])
        o_val = float(df_m15["open"].iloc[-1])
        h_val = float(df_m15["high"].iloc[-1])
        l_val = float(df_m15["low"].iloc[-1])

        # ATR M15
        atr     = calculate_atr(df_m15, 14)
        atr_val = float(atr.iloc[-1])

        # MACD histogram — arah naik atau turun
        _, _, histogram = calculate_macd(df_m15)
        hist_curr = float(histogram.iloc[-1])
        hist_prev = float(histogram.iloc[-2])
        macd_up   = hist_curr > hist_prev and hist_curr > 0
        macd_down = hist_curr < hist_prev and hist_curr < 0

        # EMA 9/21
        ema9  = calculate_ema(df_m15, 9)
        ema21 = calculate_ema(df_m15, 21)
        e9    = float(ema9.iloc[-1])
        e21   = float(ema21.iloc[-1])

        # Candle pattern
        has_bull_pattern, has_bear_pattern = detect_candle_pattern(df_m15)

        # Cek proximity S/R dari M15 close (lebih presisi)
        thr_m15 = 1.5 * atr_val
        near_support    = any(abs(close - z) <= thr_m15 for z in sup_zones)
        near_resistance = any(abs(close - z) <= thr_m15 for z in res_zones)

        # Slope MACD untuk log
        macd_slope = round(hist_curr - hist_prev, 6)

        return {
            "open_m15":           round(o_val, 4),
            "high_m15":           round(h_val, 4),
            "low_m15":            round(l_val, 4),
            "close_m15":          round(close, 4),
            "volume_m15":         round(float(df_m15["volume"].iloc[-1]), 2),
            "ema_9_m15":          round(e9, 4),
            "ema_21_m15":         round(e21, 4),
            "ema_bias":           "buy" if e9 > e21 else ("sell" if e9 < e21 else "hold"),
            "macd_histogram_m15": round(hist_curr, 6),
            "macd_slope":         macd_slope,
            "macd_up":            macd_up,
            "macd_down":          macd_down,
            "has_bull_pattern":   has_bull_pattern,
            "has_bear_pattern":   has_bear_pattern,
            "near_support_m15":   near_support,
            "near_resistance_m15": near_resistance,
            "atr_m15":            round(atr_val, 5),
            # legacy fields (untuk kompatibilitas DB)
            "rsi_m15":            0.0,
            "rsi_bias":           "hold",
            "rsi_slope":          0.0,
            "macd_m15":           round(hist_curr, 6),
            "macd_signal_m15":    0.0,
            "macd_bias":          "buy" if macd_up else ("sell" if macd_down else "hold"),
            "bb_upper_m15":       0.0,
            "bb_middle_m15":      0.0,
            "bb_lower_m15":       0.0,
            "bb_bias":            "hold",
        }

    # ─── Main ─────────────────────────────────────────────────────────────

    async def get_signal(self, db: AsyncSession) -> dict:
        logger.info(f"[{self.symbol}] Menganalisa sinyal — Precision Strategy")

        # Filter jam trading — hanya jam terbaik
        now_hour = datetime.now(timezone.utc).hour
        if now_hour not in BEST_HOURS_UTC:
            logger.info(
                f"[{self.symbol}] Jam {now_hour} UTC bukan jam trading terbaik "
                f"(best hours: {sorted(BEST_HOURS_UTC)}) — skip"
            )
            return {"symbol": self.symbol, "signal": "hold", "sl": None, "tp1": None, "tp2": None}

        loop = asyncio.get_event_loop()
        df_h1, df_m15 = await loop.run_in_executor(None, self._fetch_all_candles)

        h1  = self._analyze_h1(df_h1)
        sup_zones = h1.pop("_sup_zones")
        res_zones = h1.pop("_res_zones")
        m15 = self._analyze_m15(df_m15, sup_zones, res_zones)

        trend      = h1["trend_h1"]
        in_sup     = h1["in_support"]
        in_res     = h1["in_resistance"]
        atr_val    = m15["atr_m15"]

        # ── Hitung Confluence Score ──
        action = "hold"
        score  = 0

        # ATR filter — minimal 8 pip
        if atr_val < 0.0008:
            logger.info(f"[{self.symbol}] ATR terlalu kecil ({atr_val:.5f}) — skip")
        elif trend == "up" and in_sup:
            # BUY: dasar 2 poin (trend kuat + di support zone)
            score = 2
            if m15["macd_up"]:              score += 1
            if m15["ema_bias"] == "buy":    score += 1
            if m15["has_bull_pattern"]:     score += 1
            if score >= 3:
                action = "buy"
        elif trend == "down" and in_res:
            # SELL: dasar 2 poin (trend kuat + di resistance zone)
            score = 2
            if m15["macd_down"]:            score += 1
            if m15["ema_bias"] == "sell":   score += 1
            if m15["has_bear_pattern"]:     score += 1
            if score >= 3:
                action = "sell"

        # ── Risk Management ──
        close = m15["close_m15"]
        atr   = m15["atr_m15"]
        if action == "buy":
            sl  = round(close - 1.2 * atr, 4)
            tp1 = round(close + 1.5 * atr, 4)
            tp2 = round(close + 2.0 * atr, 4)
        elif action == "sell":
            sl  = round(close + 1.2 * atr, 4)
            tp1 = round(close - 1.5 * atr, 4)
            tp2 = round(close - 2.0 * atr, 4)
        else:
            sl = tp1 = tp2 = None

        result = {
            "symbol":        self.symbol,
            "signal":        action,
            "sl":            sl,
            "tp1":           tp1,
            "tp2":           tp2,
            "timestamp_h1":  df_h1["time"].iloc[-1],
            "timestamp_m15": df_m15["time"].iloc[-1],
            **h1,
            **{k: v for k, v in m15.items()
               if not k.startswith("macd_up") and not k.startswith("macd_down")
               and not k.startswith("has_") and not k.startswith("near_")},
        }

        logger.info(
            f"[{self.symbol}] Signal: {action.upper()} | Score: {score}/5 | "
            f"Trend H1: {trend} | InSup: {in_sup} | InRes: {in_res} | "
            f"ATR: {atr_val:.5f} | Jam: {now_hour} UTC | "
            f"MACD_up={m15['macd_up']} MACD_dn={m15['macd_down']} "
            f"EMA_bias={m15['ema_bias']} Pattern_bull={m15['has_bull_pattern']} "
            f"Pattern_bear={m15['has_bear_pattern']} | "
            f"SL: {sl} | TP1: {tp1} | TP2: {tp2}"
        )

        repo   = TradeSignalRepository(db)
        record = await repo.save(result)
        logger.success(
            f"[{self.symbol}] Signal tersimpan | ID: {record.id} | "
            f"Signal: {action.upper()} | Score: {score}/5"
        )

        # ── Simpan candle pattern ke DB untuk dataset ML ──
        candle_info = classify_candle(df_m15)
        await CandlePatternRepository(db).save({
            "symbol":        self.symbol,
            "timeframe":     "M15",
            "candle_time":   df_m15["time"].iloc[-1],
            "open":          m15["open_m15"],
            "high":          m15["high_m15"],
            "low":           m15["low_m15"],
            "close":         m15["close_m15"],
            "volume":        m15["volume_m15"],
            "body":          candle_info["body"],
            "upper_shadow":  candle_info["upper_shadow"],
            "lower_shadow":  candle_info["lower_shadow"],
            "candle_dir":    candle_info["candle_dir"],
            "pattern_name":  candle_info["pattern_name"],
            "trend_h1":      trend,
            "in_support":    in_sup,
            "in_resistance": in_res,
            "score":         score,
            "outcome":       None,  # diisi nanti setelah trade close
        })
        logger.debug(f"[{self.symbol}] Candle pattern tersimpan: {candle_info['pattern_name']}")

        if action in ("buy", "sell"):
            order_result = await TradeOrderUsecase().execute(
                db=db,
                signal_id=record.id,
                action=action,
                sl=sl,
                tp=tp1,
                created_by="robot",
            )
            logger.info(f"[{self.symbol}] Order result: {order_result}")

        return result

    async def list_signals(
        self,
        db: AsyncSession,
        filters: list[dict] | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        repo = TradeSignalRepository(db)
        return await repo.get_list(filters=filters, page=page, page_size=page_size)

    async def get_signal_by_id(self, db: AsyncSession, signal_id: int):
        repo = TradeSignalRepository(db)
        return await repo.get_by_id(signal_id)

    async def get_dashboard(
        self,
        db: AsyncSession,
        date_from: date,
        date_to: date,
        signal: str | None = None,
    ) -> dict:
        repo = TradeSignalRepository(db)
        return await repo.get_dashboard(
            date_from=date_from,
            date_to=date_to,
            signal=signal,
        )
