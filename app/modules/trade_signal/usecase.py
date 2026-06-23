import asyncio
from datetime import date, datetime, timedelta, timezone
import MetaTrader5 as mt5
import pandas as pd
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.utils.indicators import classify_candle
from app.utils.analysis import analyze_h1, analyze_h4, analyze_m15, get_confluence_score, analyze_d1
from app.modules.candle_pattern.repository import CandlePatternRepository
from app.modules.trade_signal.repository import TradeSignalRepository
from app.modules.trade_order.usecase import TradeOrderUsecase
from app.modules.trade_order.repository import TradeOrderRepository


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

# EMA50 slope minimal X pip dalam 3 candle — dari sweep backtest
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

    def _fetch_all_candles(self) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        ok = mt5.initialize(
            path=settings.MT5_PATH,
            login=settings.MT5_LOGIN,
            password=settings.MT5_PASSWORD,
            server=settings.MT5_SERVER,
        )
        if not ok:
            raise RuntimeError(f"MT5 initialize gagal: {mt5.last_error()}")
        try:
            df_d1  = self._fetch_candles("1d",  count=400)   # 200 + buffer untuk EMA200 D1
            df_h4  = self._fetch_candles("4h",  count=220)   # 200 + buffer konfirmasi H4
            df_h1  = self._fetch_candles("1h",  count=740)   # 720 + buffer (~1 bulan)
            df_m15 = self._fetch_candles("15m", count=100)
            return df_d1, df_h4, df_h1, df_m15
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

    # ─── H1: Trend + S/R Zone ─────────────────────────────────────────────

    # ─── Main ─────────────────────────────────────────────────────────────

    async def get_signal(self, db: AsyncSession) -> dict:
        logger.info(f"[{self.symbol}] Menganalisa sinyal — Precision Strategy")

        loop = asyncio.get_event_loop()
        df_d1, df_h4, df_h1, df_m15 = await loop.run_in_executor(None, self._fetch_all_candles)

        trend_d1  = analyze_d1(df_d1)
        trend_h4  = analyze_h4(df_h4)
        h1        = analyze_h1(df_h1)
        sup_zones = h1.pop("_sup_zones")
        res_zones = h1.pop("_res_zones")
        m15       = analyze_m15(df_m15, sup_zones, res_zones)

        # Semua analisa pakai candle CLOSED (-2), bukan candle berjalan
        df_m15_closed = df_m15.iloc[:-1]
        df_h1_closed  = df_h1.iloc[:-1]

        trend   = h1["trend_h1"]
        in_sup  = h1["in_support"]
        in_res  = h1["in_resistance"]
        atr_val = m15["atr_m15"]

        # ── ATR filter — minimal 5 pip (dilonggarkan dari 8) ──
        action = "hold"
        score  = 0

        if atr_val < 0.0005:
            logger.info(f"[{self.symbol}] ATR terlalu kecil ({atr_val:.5f}) — skip")
        else:
            buy_score  = get_confluence_score("buy",  h1, m15, trend_h4, trend_d1)
            sell_score = get_confluence_score("sell", h1, m15, trend_h4, trend_d1)

            if buy_score >= 5:
                action = "buy"
                score  = buy_score
            elif sell_score >= 5:
                action = "sell"
                score  = sell_score

        # ── Adaptive SL/TP berdasarkan score (skala 0-9) ──
        # Score 5-6   → SL 1.2x, TP 2.4x  (RR 1:2)
        # Score 7     → SL 1.0x, TP 2.5x  (RR 1:2.5)
        # Score >=8   → SL 0.8x, TP 2.6x  (RR 1:3.25)
        close   = m15["close_m15"]
        atr     = m15["atr_m15"]
        ema21   = m15["ema_21_m15"]

        # Entry pullback: pasang LIMIT di EMA21 M15 (harga retrace dulu, baru entry).
        # Kalau harga sudah melewati EMA ke arah favorable, pakai close (≈ market).
        if action == "buy":
            entry_target = round(min(ema21, close), 4)   # entry tidak di atas close
        elif action == "sell":
            entry_target = round(max(ema21, close), 4)   # entry tidak di bawah close
        else:
            entry_target = None

        if action != "hold":
            if score >= 8:
                sl_mult, tp_mult = 0.8, 2.6
            elif score == 7:
                sl_mult, tp_mult = 1.0, 2.5
            else:
                sl_mult, tp_mult = 1.2, 2.4

            # SL/TP dihitung dari entry_target (titik keisi), bukan dari close
            if action == "buy":
                sl  = round(entry_target - sl_mult * atr, 4)
                tp1 = round(entry_target + tp_mult * atr, 4)
            else:
                sl  = round(entry_target + sl_mult * atr, 4)
                tp1 = round(entry_target - tp_mult * atr, 4)
        else:
            sl = tp1 = None

        result = {
            "symbol":        self.symbol,
            "signal":        action,
            "sl":            sl,
            "tp1":           tp1,
            "timestamp_h1":  df_h1_closed["time"].iloc[-1],
            "timestamp_m15": df_m15_closed["time"].iloc[-1],
            **h1,
            **{k: v for k, v in m15.items()
               if not k.startswith("macd_up") and not k.startswith("macd_down")
               and not k.startswith("has_") and not k.startswith("near_")},
        }

        sl_str  = f"{sl:.5f}"  if sl  is not None else "None"
        tp1_str = f"{tp1:.5f}" if tp1 is not None else "None"
        logger.info(
            f"[{self.symbol}] Signal: {action.upper()} | Score: {score}/9 | "
            f"Trend H1: {trend} | InSup: {in_sup} | InRes: {in_res} | "
            f"ATR: {atr_val:.5f} | "
            f"MACD_up={m15['macd_up']} MACD_dn={m15['macd_down']} "
            f"EMA_bias={m15['ema_bias']} Pattern_bull={m15['has_bull_pattern']} "
            f"Pattern_bear={m15['has_bear_pattern']} | "
            f"SL: {sl_str} | TP1: {tp1_str}"
        )

        if action not in ("buy", "sell"):
            return result

        # ── Pasang pending LIMIT di EMA21 (pullback entry, anti-chasing) ──
        # Signal & candle TIDAK disimpan sekarang. Baru disimpan saat LIMIT
        # benar-benar keisi (lihat monitor_limit_entries). Order pending yang
        # tidak keisi akan dihapus saat expire → DB tetap bersih.
        order_repo = TradeOrderRepository(db)

        # Hindari menumpuk pending LIMIT — kalau sudah ada yang aktif, skip.
        existing = await order_repo.get_pending_limits(self.symbol)
        if existing:
            logger.info(f"[{self.symbol}] Sudah ada {len(existing)} pending LIMIT aktif — skip buat baru")
            return result

        expire_at = datetime.now(timezone.utc) + timedelta(minutes=settings.LIMIT_EXPIRE_MINUTES_M15)
        pending   = await TradeOrderUsecase().create_pending_limit(
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

    # ─── Monitor pending LIMIT: eksekusi saat harga retrace ke target ───────

    async def monitor_limit_entries(self, db: AsyncSession) -> None:
        """
        Cek pending LIMIT robot. Untuk tiap order:
          • Expired           → hapus (DB bersih, tidak ada jejak)
          • Harga retrace ke target & signal masih valid → kirim market order,
            simpan signal + candle + isi relasinya, status open
          • Harga retrace tapi signal sudah tidak valid → hapus (cancelled)
        Analisa di-refresh dari candle closed terbaru tiap dipanggil.
        """
        order_repo = TradeOrderRepository(db)
        pending    = await order_repo.get_pending_limits(self.symbol)
        if not pending:
            return

        now = datetime.now(timezone.utc)
        loop = asyncio.get_event_loop()

        # Ambil harga live + data analisa sekali untuk semua order
        df_d1, df_h4, df_h1, df_m15 = await loop.run_in_executor(None, self._fetch_all_candles)
        tick_price = await loop.run_in_executor(None, self._fetch_tick_price)
        if tick_price is None:
            logger.warning(f"[{self.symbol}] Tidak bisa ambil harga live untuk cek LIMIT")
            return

        trend_d1  = analyze_d1(df_d1)
        trend_h4  = analyze_h4(df_h4)
        h1        = analyze_h1(df_h1)
        sup_zones = h1.pop("_sup_zones")
        res_zones = h1.pop("_res_zones")
        m15       = analyze_m15(df_m15, sup_zones, res_zones)
        df_m15_closed = df_m15.iloc[:-1]
        df_h1_closed  = df_h1.iloc[:-1]

        for order in pending:
            # 1. Expire → hapus
            expire_at = order.expire_at
            if expire_at is not None and expire_at.tzinfo is None:
                expire_at = expire_at.replace(tzinfo=timezone.utc)
            if expire_at is not None and now >= expire_at:
                await order_repo.delete_order(order)
                logger.info(f"[{self.symbol}] LIMIT #{order.id} EXPIRED (tidak retrace) — dihapus")
                continue

            # 2. Cek harga sudah retrace ke target?
            target    = order.entry_target
            triggered = (order.action == "buy"  and tick_price <= target) or \
                        (order.action == "sell" and tick_price >= target)
            if not triggered:
                continue

            # 3. Re-validasi signal sekarang (candle closed terbaru)
            score = get_confluence_score(order.action, h1, m15, trend_h4, trend_d1)
            if score < 5:
                await order_repo.delete_order(order)
                logger.warning(
                    f"[{self.symbol}] LIMIT #{order.id} CANCELLED — signal tidak valid lagi "
                    f"saat retrace | {order.action.upper()} | score={score}/9"
                )
                continue

            # 4. Kirim market order di harga retrace sekarang
            order_usecase = TradeOrderUsecase()
            mt5_result    = await order_usecase.execute_without_save(
                action=order.action, sl=order.sl, tp=order.tp
            )
            if mt5_result.get("status") != "open":
                logger.warning(
                    f"[{self.symbol}] LIMIT #{order.id} gagal eksekusi ke MT5 | {mt5_result.get('comment')}"
                )
                continue

            # 5. Order keisi → simpan signal + candle, link ke order ini
            result = {
                "symbol":        self.symbol,
                "signal":        order.action,
                "sl":            order.sl,
                "tp1":           order.tp,
                "timestamp_h1":  df_h1_closed["time"].iloc[-1],
                "timestamp_m15": df_m15_closed["time"].iloc[-1],
                **h1,
                **{k: v for k, v in m15.items()
                   if not k.startswith("macd_up") and not k.startswith("macd_down")
                   and not k.startswith("has_") and not k.startswith("near_")},
            }
            record = await TradeSignalRepository(db).save(result)

            candle_info   = classify_candle(df_m15_closed)
            candle_record = await CandlePatternRepository(db).save({
                "signal_id":     record.id,
                "symbol":        self.symbol,
                "timeframe":     "M15",
                "candle_time":   df_m15_closed["time"].iloc[-1],
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
                "trend_h1":      h1["trend_h1"],
                "in_support":    h1["in_support"],
                "in_resistance": h1["in_resistance"],
                "score":         score,
                "outcome":       None,
            })

            # 6. Update order pending → open + isi relasi
            order.signal_id = record.id
            order.candle_id = candle_record.id
            order.status    = mt5_result["status"]
            order.price     = mt5_result.get("price", 0.0)
            order.ticket    = mt5_result.get("ticket")
            order.comment   = f"LIMIT filled @ {tick_price} | score={score}/9 | {mt5_result.get('comment','')}"
            await db.flush()

            logger.success(
                f"[{self.symbol}] LIMIT #{order.id} FILLED | {order.action.upper()} @ {tick_price} "
                f"| Score: {score}/9 | Ticket: {order.ticket} | Signal ID: {record.id}"
            )

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
