"""
XAUUSD 5m Scalping Strategy
─────────────────────────────────────────────────────────────────
H1 (500 candle) — Trend filter
  • EMA50 slope + harga vs EMA200 → trend UP / DOWN / SIDEWAYS
  • Swing H1 → zona Support / Resistance
  • RSI H1 norm > 20 (searah trend kuat)
  • ADX H1 >= 32 (trend kuat, bukan sideways)

M5 — Entry timing
  • EMA50 > EMA200 (golden cross area)
  • RSI > 50 (buy) / RSI < 50 (sell)
  • BB Width 1%-3% (volatilitas optimal)
  • Bullish / bearish engulfing (atau pin bar / marubozu)

Score (0-6):
  +1  Trend H1 searah           ← wajib
  +1  H4 searah / netral        ← H4 berlawanan = blocker
  +1  Harga di S/R zone H1
  +1  EMA50 > EMA200 M5 searah
  +1  RSI searah (>50 buy / <50 sell)
  +1  Candle pattern konfirmasi
  Signal masuk kalau score >= 5

Filter tambahan (ADVANCED — dari backtest 97k candle):
  • Skip jam 10,11,16,17 UTC (WR buruk)
  • BB Width harus 1%-3%
  • RSI H1 norm > 20  (RSI H1 searah trend, min 20 poin dari midline)
  • ADX H1 >= 32      (trend H1 kuat)
  Hasil: WR ~54%, PF 2.38

SL/TP:
  SL = 1.0 × ATR M5
  TP = 2.0 × ATR M5  (RR 1:2)

ATR filter: minimal 3.0 (dari backtest: ATR < 3.0 WR 31.9%)
"""

import asyncio
from datetime import datetime, timedelta, timezone
import MetaTrader5 as mt5
import pandas as pd
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.utils.indicators import (
    calculate_ema,
    calculate_atr,
    calculate_rsi,
    calculate_adx,
    calculate_bbw,
    calculate_macd,
    find_swing_levels,
    detect_candle_pattern,
    classify_candle,
)
from app.utils.analysis import analyze_h4, cluster_zones
from app.modules.trade_signal_xauusd.repository import TradeSignalXauusdRepository
from app.modules.candle_pattern.repository import CandlePatternRepository
from app.modules.trade_order.usecase import TradeOrderUsecase
from app.modules.trade_order.repository import TradeOrderRepository

ATR_MIN          = 3.0   # dari backtest: ATR < 3.0 WR hanya 31.9%
SCORE_MIN        = 5     # hanya order kalau score >= 5
BAD_HOURS_UTC    = {10, 11, 16, 17}   # jam WR buruk dari backtest
BBW_MIN          = 1.0   # BB Width minimum %
BBW_MAX          = 3.0   # BB Width maksimum %
RSI_H1_NORM_MIN  = 20    # RSI H1 harus min 20 poin searah trend dari midline 50
ADX_H1_MIN       = 32    # ADX H1 harus >= 32 (trend kuat)


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

    def _fetch_tick_price(self) -> float | None:
        """Harga live (ask) untuk cek apakah harga sudah retrace ke target LIMIT."""
        if not mt5.initialize(path=settings.MT5_PATH):
            return None
        try:
            tick = mt5.symbol_info_tick(self.symbol)
            return float(tick.ask) if tick else None
        finally:
            mt5.shutdown()

    # ─── Analyze M5 ───────────────────────────────────────────────────────

    @staticmethod
    def _analyze_m5(df_m5: pd.DataFrame, sup_zones: list, res_zones: list) -> dict:
        # Candle closed (-2), bukan candle berjalan (-1)
        close = float(df_m5["close"].iloc[-2])
        o_val = float(df_m5["open"].iloc[-2])
        h_val = float(df_m5["high"].iloc[-2])
        l_val = float(df_m5["low"].iloc[-2])

        ema50  = calculate_ema(df_m5, 50)
        ema200 = calculate_ema(df_m5, 200)
        atr    = calculate_atr(df_m5, 14)
        rsi    = calculate_rsi(df_m5, 14)

        e50     = float(ema50.iloc[-2])
        e200    = float(ema200.iloc[-2])
        atr_val = float(atr.iloc[-2])
        rsi_val = float(rsi.iloc[-2])

        # Retrace ke EMA50 M5: harga dalam 1.5 ATR dari EMA50
        near_ema50 = abs(close - e50) <= 1.5 * atr_val

        has_bull, has_bear = detect_candle_pattern(df_m5.iloc[:-1])
        bbw_val = calculate_bbw(df_m5.iloc[:-1], period=20, std=2.0)

        return {
            "open_m5":          round(o_val, 2),
            "high_m5":          round(h_val, 2),
            "low_m5":           round(l_val, 2),
            "close_m5":         round(close, 2),
            "volume_m5":        round(float(df_m5["volume"].iloc[-2]), 2),
            "ema_50_m5":        round(e50, 2),
            "ema_200_m5":       round(e200, 2),
            "rsi_m5":           round(rsi_val, 2),
            "atr_m5":           round(atr_val, 4),
            "bbw":              round(bbw_val, 4),
            "near_ema50":       near_ema50,
            "ema_cross_bull":   e50 > e200,
            "ema_cross_bear":   e50 < e200,
            "has_bull_pattern": has_bull,
            "has_bear_pattern": has_bear,
            "bullish_close":    close > o_val,   # candle closed bullish
            "bearish_close":    close < o_val,   # candle closed bearish
        }

    # ─── Confluence score ─────────────────────────────────────────────────

    @staticmethod
    def _score(action: str, h1: dict, m5: dict, trend_h4: str) -> int:
        """
        Score 0-8 untuk XAUUSD 5m strategy.

        Blocker (return 0):
          • Trend H1 tidak searah
          • H4 berlawanan tegas
          • MACD H1 berlawanan tegas  ← BARU: momentum besar harus searah

          +1  Trend H1 searah            ← wajib
          +1  H4 searah / netral
          +1  Harga di S/R zone H1
          +1  MACD H1 searah             ← BARU
          +1  EMA50 vs EMA200 M5 searah
          +1  RSI M5 searah (>50 buy / <50 sell)
          +1  Candle pattern konfirmasi
          +1  Bounce confirmed dari S/R  ← BARU
        """
        trend_h1 = h1["trend_h1"]
        macd_h1  = h1.get("macd_hist_h1", 0.0)

        if action == "buy":
            if trend_h1 != "up":      return 0
            if trend_h4 == "down":    return 0
            if macd_h1 < 0:           return 0   # momentum H1 berlawanan

            score = 1                                        # H1 up
            if trend_h4 == "up":      score += 1            # H4 konfirmasi
            if h1["in_support"]:      score += 1            # di S/R zone
            if macd_h1 > 0:           score += 1            # MACD H1 searah
            if m5["ema_cross_bull"]:  score += 1            # EMA50 > EMA200 M5
            if m5["rsi_m5"] > 50:     score += 1            # RSI konfirmasi
            if m5["has_bull_pattern"]: score += 1           # candle pattern
            if h1["in_support"] and m5.get("bullish_close"): score += 1  # bounce

        elif action == "sell":
            if trend_h1 != "down":    return 0
            if trend_h4 == "up":      return 0
            if macd_h1 > 0:           return 0   # momentum H1 berlawanan

            score = 1
            if trend_h4 == "down":    score += 1
            if h1["in_resistance"]:   score += 1
            if macd_h1 < 0:           score += 1            # MACD H1 searah
            if m5["ema_cross_bear"]:  score += 1
            if m5["rsi_m5"] < 50:     score += 1
            if m5["has_bear_pattern"]: score += 1
            if h1["in_resistance"] and m5.get("bearish_close"): score += 1  # bounce
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
        rsi_h1 = calculate_rsi(df_h1, 14)
        adx_h1 = calculate_adx(df_h1, 14)

        # Candle closed (-2), bukan candle berjalan (-1)
        e50        = float(ema50.iloc[-2])
        e200       = float(ema200.iloc[-2])
        atr_val_h1 = float(atr_h1.iloc[-2])
        rsi_val_h1 = float(rsi_h1.iloc[-2])
        adx_val_h1 = float(adx_h1.iloc[-2])
        slope      = float(ema50.iloc[-2]) - float(ema50.iloc[-5])
        close      = float(df_h1["close"].iloc[-2])

        # MACD H1 — konfirmasi momentum besar (candle closed)
        _, _, macd_hist_h1 = calculate_macd(df_h1)
        macd_h1_curr = float(macd_hist_h1.iloc[-2])

        if slope > SLOPE_THRESHOLD and (close > e200 or e50 > e200):
            trend = "up"
        elif slope < -SLOPE_THRESHOLD and (close < e200 or e50 < e200):
            trend = "down"
        else:
            trend = "sideways"

        res_levels, sup_levels = find_swing_levels(df_h1.iloc[:-1], lookback=500, window=4)
        res_zones = cluster_zones(res_levels)
        sup_zones = cluster_zones(sup_levels)

        thr           = 2.0 * atr_val_h1
        in_resistance = any(abs(close - z) <= thr for z in res_zones)
        in_support    = any(abs(close - z) <= thr for z in sup_zones)

        return {
            "trend_h1":      trend,
            "ema_50_h1":     round(e50, 2),
            "ema_200_h1":    round(e200, 2),
            "in_support":    in_support,
            "in_resistance": in_resistance,
            "atr_h1":        round(atr_val_h1, 4),
            "rsi_h1":        round(rsi_val_h1, 2),
            "adx_h1":        round(adx_val_h1, 2),
            "macd_hist_h1":  round(macd_h1_curr, 6),
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

        # Semua analisa pakai candle CLOSED (-2), bukan candle berjalan
        df_m5_closed = df_m5.iloc[:-1]

        atr_val      = m5["atr_m5"]
        bbw_val      = m5["bbw"]
        pattern_name = classify_candle(df_m5_closed)["pattern_name"]
        action       = "hold"
        score        = 0
        jam_utc      = df_m5_closed["time"].iloc[-1].hour

        # ── Filter ADVANCED (dari backtest 97k candle, WR ~54%) ──────────────
        rsi_h1_val  = h1["rsi_h1"]
        adx_h1_val  = h1["adx_h1"]
        trend_h1    = h1["trend_h1"]

        # RSI H1 norm = jarak RSI dari midline 50, searah trend
        if trend_h1 == "down":
            rsi_h1_norm = 50 - rsi_h1_val
        elif trend_h1 == "up":
            rsi_h1_norm = rsi_h1_val - 50
        else:
            rsi_h1_norm = 0.0

        if atr_val < ATR_MIN:
            logger.info(f"[{self.symbol}] ATR M5 terlalu kecil ({atr_val:.4f}) — skip")
        elif pattern_name == "doji":
            logger.info(f"[{self.symbol}] Candle doji — skip (doji WR 23.8%)")
        elif jam_utc in BAD_HOURS_UTC:
            logger.info(f"[{self.symbol}] Jam {jam_utc} UTC diblokir — skip (WR buruk)")
        elif not (BBW_MIN <= bbw_val < BBW_MAX):
            logger.info(f"[{self.symbol}] BBW {bbw_val:.2f}% di luar range {BBW_MIN}-{BBW_MAX}% — skip")
        elif rsi_h1_norm <= RSI_H1_NORM_MIN:
            logger.info(f"[{self.symbol}] RSI H1 norm {rsi_h1_norm:.1f} <= {RSI_H1_NORM_MIN} — skip (momentum H1 lemah)")
        elif adx_h1_val < ADX_H1_MIN:
            logger.info(f"[{self.symbol}] ADX H1 {adx_h1_val:.1f} < {ADX_H1_MIN} — skip (trend H1 lemah/sideways)")
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
        ema50 = m5["ema_50_m5"]

        # Entry pullback: LIMIT di EMA50 M5 (harga retrace dulu, baru entry)
        if action == "buy":
            entry_target = round(min(ema50, close), 2)
        elif action == "sell":
            entry_target = round(max(ema50, close), 2)
        else:
            entry_target = None

        if action != "hold":
            sl_mult = 1.0
            tp_mult = 2.0
            # SL/TP dihitung dari entry_target (titik keisi), bukan dari close
            if action == "buy":
                sl  = round(entry_target - sl_mult * atr, 2)
                tp1 = round(entry_target + tp_mult * atr, 2)
            else:
                sl  = round(entry_target + sl_mult * atr, 2)
                tp1 = round(entry_target - tp_mult * atr, 2)
        else:
            sl = tp1 = None

        result = {
            "symbol":       self.symbol,
            "signal":       action,
            "sl":           sl,
            "tp1":          tp1,
            "score":        score,
            "jam_utc":      jam_utc,
            "timestamp_h1": df_h1["time"].iloc[-2],
            "timestamp_m5": df_m5_closed["time"].iloc[-1],
            **h1,
            **{k: v for k, v in m5.items()
               if not k.startswith("near_") and not k.startswith("ema_cross")},
        }

        sl_str  = f"{sl:.2f}"  if sl  is not None else "None"
        tp1_str = f"{tp1:.2f}" if tp1 is not None else "None"
        logger.info(
            f"[{self.symbol}] Signal: {action.upper()} | Score: {score}/8 | "
            f"Trend H1: {h1['trend_h1']} | H4: {trend_h4} | "
            f"RSI_H1: {rsi_h1_val:.1f} (norm {rsi_h1_norm:.1f}) | ADX_H1: {adx_h1_val:.1f} | "
            f"BBW: {bbw_val:.2f}% | Jam UTC: {jam_utc} | "
            f"InSup: {h1['in_support']} | InRes: {h1['in_resistance']} | "
            f"RSI M5: {m5['rsi_m5']:.1f} | ATR: {atr_val:.4f} | SL: {sl_str} | TP1: {tp1_str}"
        )

        if action not in ("buy", "sell"):
            return result

        # ── Pasang pending LIMIT di EMA50 M5 (pullback entry, anti-chasing) ──
        # Signal & candle baru disimpan saat LIMIT keisi (monitor_limit_entries).
        order_repo = TradeOrderRepository(db)
        existing   = await order_repo.get_pending_limits(self.symbol)
        if existing:
            logger.info(f"[{self.symbol}] Sudah ada {len(existing)} pending LIMIT aktif — skip buat baru")
            return result

        expire_at = datetime.now(timezone.utc) + timedelta(minutes=settings.LIMIT_EXPIRE_MINUTES_M5)
        pending   = await TradeOrderUsecase(symbol=self.symbol, lot=settings.XAUUSD_LOT_SIZE).create_pending_limit(
            db=db,
            signal_id=0,
            candle_id=None,
            action=action,
            entry_target=entry_target,
            sl=sl,
            tp=tp1,
            expire_at=expire_at,
            created_by="robot",
        )
        logger.info(f"[{self.symbol}] Pending LIMIT result: {pending}")

        return result

    # ─── Monitor pending LIMIT XAUUSD: eksekusi saat harga retrace ──────────

    async def monitor_limit_entries(self, db: AsyncSession) -> None:
        """Cek pending LIMIT XAUUSD. Sama seperti EURUSD: expire→hapus,
        retrace+valid→eksekusi & simpan signal+candle, retrace+invalid→hapus."""
        order_repo = TradeOrderRepository(db)
        pending    = await order_repo.get_pending_limits(self.symbol)
        if not pending:
            return

        now  = datetime.now(timezone.utc)
        loop = asyncio.get_event_loop()

        df_h4, df_h1, df_m5 = await loop.run_in_executor(None, self._fetch_all_candles)
        tick_price = await loop.run_in_executor(None, self._fetch_tick_price)
        if tick_price is None:
            logger.warning(f"[{self.symbol}] Tidak bisa ambil harga live untuk cek LIMIT")
            return

        trend_h4  = analyze_h4(df_h4)
        h1        = self._analyze_h1(df_h1)
        sup_zones = h1.pop("_sup_zones")
        res_zones = h1.pop("_res_zones")
        m5        = self._analyze_m5(df_m5, sup_zones, res_zones)
        df_m5_closed = df_m5.iloc[:-1]

        for order in pending:
            expire_at = order.expire_at
            if expire_at is not None and expire_at.tzinfo is None:
                expire_at = expire_at.replace(tzinfo=timezone.utc)
            if expire_at is not None and now >= expire_at:
                await order_repo.delete_order(order)
                logger.info(f"[{self.symbol}] LIMIT #{order.id} EXPIRED (tidak retrace) — dihapus")
                continue

            target    = order.entry_target
            triggered = (order.action == "buy"  and tick_price <= target) or \
                        (order.action == "sell" and tick_price >= target)
            if not triggered:
                continue

            score = self._score(order.action, h1, m5, trend_h4)
            if score < SCORE_MIN:
                await order_repo.delete_order(order)
                logger.warning(
                    f"[{self.symbol}] LIMIT #{order.id} CANCELLED — signal tidak valid lagi "
                    f"saat retrace | {order.action.upper()} | score={score}/8"
                )
                continue

            order_usecase = TradeOrderUsecase(symbol=self.symbol, lot=settings.XAUUSD_LOT_SIZE)
            mt5_result    = await order_usecase.execute_without_save(
                action=order.action, sl=order.sl, tp=order.tp
            )
            if mt5_result.get("status") != "open":
                logger.warning(
                    f"[{self.symbol}] LIMIT #{order.id} gagal eksekusi ke MT5 | {mt5_result.get('comment')}"
                )
                continue

            jam_utc = df_m5_closed["time"].iloc[-1].hour
            result  = {
                "symbol":       self.symbol,
                "signal":       order.action,
                "sl":           order.sl,
                "tp1":          order.tp,
                "score":        score,
                "jam_utc":      jam_utc,
                "timestamp_h1": df_h1["time"].iloc[-2],
                "timestamp_m5": df_m5_closed["time"].iloc[-1],
                **h1,
                **{k: v for k, v in m5.items()
                   if not k.startswith("near_") and not k.startswith("ema_cross")},
            }
            record = await TradeSignalXauusdRepository(db).save(result)

            candle_info   = classify_candle(df_m5_closed)
            candle_record = await CandlePatternRepository(db).save({
                "signal_id":     record.id,
                "symbol":        self.symbol,
                "timeframe":     "M5",
                "candle_time":   df_m5_closed["time"].iloc[-1],
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

            order.signal_id = record.id
            order.candle_id = candle_record.id
            order.status    = mt5_result["status"]
            order.price     = mt5_result.get("price", 0.0)
            order.ticket    = mt5_result.get("ticket")
            order.comment   = f"LIMIT filled @ {tick_price} | score={score}/8 | {mt5_result.get('comment','')}"
            await db.flush()

            logger.success(
                f"[{self.symbol}] LIMIT #{order.id} FILLED | {order.action.upper()} @ {tick_price} "
                f"| Score: {score}/8 | Ticket: {order.ticket} | Signal ID: {record.id}"
            )

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
