"""
XAUUSD 5m Scalping Strategy
─────────────────────────────────────────────────────────────────
H1 (500 candle) — Trend filter
  • EMA50 slope + harga vs EMA200 → trend UP / DOWN / SIDEWAYS
  • Swing H1 → zona Support / Resistance

M5 — Entry timing
  • EMA50 > EMA200 (golden cross area)
  • Harga retrace / bounce ke zona EMA50 M5
  • RSI > 50 (buy) / RSI < 50 (sell)
  • Bullish / bearish engulfing (atau pin bar / marubozu)

Score (0-6):
  +1  Trend H1 searah           ← wajib
  +1  H4 searah / netral        ← H4 berlawanan = blocker
  +1  Harga di S/R zone H1
  +1  EMA50 > EMA200 M5 searah
  +1  RSI searah (>50 buy / <50 sell)
  +1  Candle pattern konfirmasi
  Signal masuk kalau score >= 5

SL/TP:
  SL = 1.0 × ATR M5
  TP = 2.0 × ATR M5  (RR 1:2)

ATR filter: minimal 0.5 (XAUUSD ~50 sen)
"""

import asyncio
import MetaTrader5 as mt5
import pandas as pd
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.utils.indicators import (
    calculate_ema,
    calculate_atr,
    calculate_rsi,
    find_swing_levels,
    detect_candle_pattern,
    classify_candle,
)
from app.utils.analysis import analyze_h4, cluster_zones
from app.modules.trade_signal_xauusd.repository import TradeSignalXauusdRepository
from app.modules.candle_pattern.repository import CandlePatternRepository
from app.modules.trade_order.usecase import TradeOrderUsecase

ATR_MIN   = 0.50   # minimal 50 sen volatilitas
SCORE_MIN = 5      # hanya order kalau score >= 5


class TradeSignalXauusdUsecase:

    def __init__(self, symbol: str | None = None):
        self.symbol = symbol or settings.XAUUSD_SYMBOL

    # ─── Fetch candle ──────────────────────────────────────────────────────

    def _fetch_all_candles(self) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        ok = mt5.initialize(
            path=settings.MT5_PATH,
            login=settings.MT5_LOGIN,
            password=settings.MT5_PASSWORD,
            server=settings.MT5_SERVER,
        )
        if not ok:
            raise RuntimeError(f"MT5 initialize gagal: {mt5.last_error()}")
        try:
            def _df(tf, count):
                rates = mt5.copy_rates_from_pos(self.symbol, tf, 0, count)
                if rates is None or len(rates) == 0:
                    raise RuntimeError(f"Gagal fetch {self.symbol} tf={tf}: {mt5.last_error()}")
                df = pd.DataFrame(rates)
                df["time"] = pd.to_datetime(df["time"], unit="s")
                df.rename(columns={"tick_volume": "volume"}, inplace=True)
                return df

            df_h4 = _df(mt5.TIMEFRAME_H4, 220)
            df_h1 = _df(mt5.TIMEFRAME_H1, 520)
            df_m5 = _df(mt5.TIMEFRAME_M5,  200)
            return df_h4, df_h1, df_m5
        finally:
            mt5.shutdown()

    # ─── Analyze M5 ───────────────────────────────────────────────────────

    @staticmethod
    def _analyze_m5(df_m5: pd.DataFrame, sup_zones: list, res_zones: list) -> dict:
        close = float(df_m5["close"].iloc[-1])
        o_val = float(df_m5["open"].iloc[-1])
        h_val = float(df_m5["high"].iloc[-1])
        l_val = float(df_m5["low"].iloc[-1])

        ema50  = calculate_ema(df_m5, 50)
        ema200 = calculate_ema(df_m5, 200)
        atr    = calculate_atr(df_m5, 14)
        rsi    = calculate_rsi(df_m5, 14)

        e50     = float(ema50.iloc[-1])
        e200    = float(ema200.iloc[-1])
        atr_val = float(atr.iloc[-1])
        rsi_val = float(rsi.iloc[-1])

        # Retrace ke EMA50 M5: harga dalam 1.5 ATR dari EMA50
        near_ema50 = abs(close - e50) <= 1.5 * atr_val

        has_bull, has_bear = detect_candle_pattern(df_m5)

        return {
            "open_m5":          round(o_val, 2),
            "high_m5":          round(h_val, 2),
            "low_m5":           round(l_val, 2),
            "close_m5":         round(close, 2),
            "volume_m5":        round(float(df_m5["volume"].iloc[-1]), 2),
            "ema_50_m5":        round(e50, 2),
            "ema_200_m5":       round(e200, 2),
            "rsi_m5":           round(rsi_val, 2),
            "atr_m5":           round(atr_val, 4),
            "near_ema50":       near_ema50,
            "ema_cross_bull":   e50 > e200,   # golden cross area
            "ema_cross_bear":   e50 < e200,   # death cross area
            "has_bull_pattern": has_bull,
            "has_bear_pattern": has_bear,
        }

    # ─── Confluence score ─────────────────────────────────────────────────

    @staticmethod
    def _score(action: str, h1: dict, m5: dict, trend_h4: str) -> int:
        """
        Score 0-6 untuk XAUUSD 5m strategy.

          +1  Trend H1 searah            ← wajib (else 0)
          +1  H4 searah / netral         ← H4 berlawanan = blocker (return 0)
          +1  Harga di S/R zone H1
          +1  EMA50 vs EMA200 M5 searah
          +1  RSI M5 searah (>50 buy / <50 sell)
          +1  Candle pattern konfirmasi
        """
        trend_h1 = h1["trend_h1"]

        if action == "buy":
            if trend_h1 != "up":      return 0
            if trend_h4 == "down":    return 0

            score = 1                                        # H1 up
            if trend_h4 == "up":      score += 1            # H4 konfirmasi
            if h1["in_support"]:      score += 1            # di S/R zone
            if m5["ema_cross_bull"]:  score += 1            # EMA50 > EMA200 M5
            if m5["rsi_m5"] > 50:     score += 1            # RSI konfirmasi
            if m5["has_bull_pattern"]: score += 1           # candle pattern

        elif action == "sell":
            if trend_h1 != "down":    return 0
            if trend_h4 == "up":      return 0

            score = 1
            if trend_h4 == "down":    score += 1
            if h1["in_resistance"]:   score += 1
            if m5["ema_cross_bear"]:  score += 1
            if m5["rsi_m5"] < 50:     score += 1
            if m5["has_bear_pattern"]: score += 1
        else:
            score = 0

        return max(0, score)

    # ─── Analyze H1 (inline — tidak pakai analyze_h1 dari utils) ─────────

    @staticmethod
    def _analyze_h1(df_h1: pd.DataFrame) -> dict:
        from app.utils.analysis import SLOPE_THRESHOLD
        ema50  = calculate_ema(df_h1, 50)
        ema200 = calculate_ema(df_h1, 200)
        atr_h1 = calculate_atr(df_h1, 14)

        e50        = float(ema50.iloc[-1])
        e200       = float(ema200.iloc[-1])
        atr_val_h1 = float(atr_h1.iloc[-1])
        slope      = float(ema50.iloc[-1]) - float(ema50.iloc[-4])
        close      = float(df_h1["close"].iloc[-1])

        if slope > SLOPE_THRESHOLD and (close > e200 or e50 > e200):
            trend = "up"
        elif slope < -SLOPE_THRESHOLD and (close < e200 or e50 < e200):
            trend = "down"
        else:
            trend = "sideways"

        res_levels, sup_levels = find_swing_levels(df_h1, lookback=500, window=4)
        res_zones = cluster_zones(res_levels)
        sup_zones = cluster_zones(sup_levels)

        thr           = 2.0 * atr_val_h1   # XAUUSD lebih volatile
        in_resistance = any(abs(close - z) <= thr for z in res_zones)
        in_support    = any(abs(close - z) <= thr for z in sup_zones)

        return {
            "trend_h1":      trend,
            "ema_50_h1":     round(e50, 2),
            "ema_200_h1":    round(e200, 2),
            "in_support":    in_support,
            "in_resistance": in_resistance,
            "atr_h1":        round(atr_val_h1, 4),
            "_sup_zones":    sup_zones,
            "_res_zones":    res_zones,
        }

    # ─── Main ─────────────────────────────────────────────────────────────

    async def get_signal(self, db: AsyncSession) -> dict:
        logger.info(f"[{self.symbol}] Menganalisa sinyal — XAUUSD 5m Strategy")

        loop = asyncio.get_event_loop()
        df_h4, df_h1, df_m5 = await loop.run_in_executor(None, self._fetch_all_candles)

        trend_h4 = analyze_h4(df_h4)
        h1       = self._analyze_h1(df_h1)
        sup_zones = h1.pop("_sup_zones")
        res_zones = h1.pop("_res_zones")
        m5        = self._analyze_m5(df_m5, sup_zones, res_zones)

        atr_val = m5["atr_m5"]
        action  = "hold"
        score   = 0

        if atr_val < ATR_MIN:
            logger.info(f"[{self.symbol}] ATR M5 terlalu kecil ({atr_val:.4f}) — skip")
        else:
            buy_score  = self._score("buy",  h1, m5, trend_h4)
            sell_score = self._score("sell", h1, m5, trend_h4)

            if buy_score >= SCORE_MIN:
                action = "buy"
                score  = buy_score
            elif sell_score >= SCORE_MIN:
                action = "sell"
                score  = sell_score

        close = m5["close_m5"]
        atr   = m5["atr_m5"]

        if action != "hold":
            sl_mult = 1.0
            tp_mult = 2.0
            if action == "buy":
                sl  = round(close - sl_mult * atr, 2)
                tp1 = round(close + tp_mult * atr, 2)
            else:
                sl  = round(close + sl_mult * atr, 2)
                tp1 = round(close - tp_mult * atr, 2)
        else:
            sl = tp1 = None

        result = {
            "symbol":       self.symbol,
            "signal":       action,
            "sl":           sl,
            "tp1":          tp1,
            "score":        score,
            "timestamp_h1": df_h1["time"].iloc[-1],
            "timestamp_m5": df_m5["time"].iloc[-1],
            **h1,
            **{k: v for k, v in m5.items()
               if not k.startswith("near_") and not k.startswith("ema_cross")},
        }

        sl_str  = f"{sl:.2f}"  if sl  is not None else "None"
        tp1_str = f"{tp1:.2f}" if tp1 is not None else "None"
        logger.info(
            f"[{self.symbol}] Signal: {action.upper()} | Score: {score}/6 | "
            f"Trend H1: {h1['trend_h1']} | H4: {trend_h4} | "
            f"InSup: {h1['in_support']} | InRes: {h1['in_resistance']} | "
            f"EMA cross bull={m5['ema_cross_bull']} | RSI={m5['rsi_m5']:.1f} | "
            f"Pattern bull={m5['has_bull_pattern']} bear={m5['has_bear_pattern']} | "
            f"ATR: {atr_val:.4f} | SL: {sl_str} | TP1: {tp1_str}"
        )

        if action not in ("buy", "sell"):
            return result

        # 1. Simpan signal
        repo   = TradeSignalXauusdRepository(db)
        record = await repo.save(result)
        logger.success(
            f"[{self.symbol}] XAUUSD signal tersimpan | ID: {record.id} | "
            f"Signal: {action.upper()} | Score: {score}/6"
        )

        # 2. Simpan candle pattern
        candle_info   = classify_candle(df_m5)
        candle_record = await CandlePatternRepository(db).save({
            "signal_id":     record.id,
            "symbol":        self.symbol,
            "timeframe":     "M5",
            "candle_time":   df_m5["time"].iloc[-1],
            "open":          m5["open_m5"],
            "high":          m5["high_m5"],
            "low":           m5["low_m5"],
            "close":         m5["close_m5"],
            "volume":        m5["volume_m5"],
            "body":          candle_info["body"],
            "upper_shadow":  candle_info["upper_shadow"],
            "lower_shadow":  candle_info["lower_shadow"],
            "candle_dir":    candle_info["candle_dir"],
            "pattern_name":  candle_info["pattern_name"],
            "trend_h1":      h1["trend_h1"],
            "in_support":    h1["in_support"],
            "in_resistance": h1["in_resistance"],
            "score":         score,
            "outcome":       None,
        })
        logger.debug(f"[{self.symbol}] Candle pattern tersimpan: {candle_info['pattern_name']} | ID: {candle_record.id}")

        # 3. Kirim order
        order_result = await TradeOrderUsecase(symbol=self.symbol, lot=settings.XAUUSD_LOT_SIZE).execute(
            db=db,
            signal_id=record.id,
            candle_id=candle_record.id,
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
        return await TradeSignalXauusdRepository(db).get_list(
            filters=filters, page=page, page_size=page_size
        )

    async def get_signal_by_id(self, db: AsyncSession, signal_id: int):
        return await TradeSignalXauusdRepository(db).get_by_id(signal_id)

    async def get_dashboard(
        self,
        db: AsyncSession,
        date_from,
        date_to,
        signal: str | None = None,
    ) -> dict:
        return await TradeSignalXauusdRepository(db).get_dashboard(
            date_from=date_from,
            date_to=date_to,
            signal=signal,
        )
