import asyncio
from datetime import date
import MetaTrader5 as mt5
import pandas as pd
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.utils.indicators import (
    calculate_ema,
    calculate_rsi,
    calculate_macd,
    calculate_atr,
    calculate_bbands,
)
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


class TradeSignalUsecase:
    """
    Strategi: Scalping Multi-Timeframe (Optimized via Sweep 5,760 kombinasi — 2021-2026)
    ──────────────────────────────────────────────────────────────────────────────────────
    H1  → Trend filter utama (EMA 50 & EMA 200)
          EMA50 > EMA200 → trend naik  (BUY only)
          EMA50 < EMA200 → trend turun (SELL only)

    M15 → Entry signal — minimal 3/4 indikator searah trend:
           • RSI        : ≤ 30 = buy bias, ≥ 70 = sell bias
           • MACD       : posisi macd_line vs signal_line
           • EMA 9/21   : posisi EMA fast vs slow
           • BB Position: harga vs middle band

    Slope Filter (need_slope=True — dari hasil sweep):
      BUY  → RSI slope > 0 AND MACD histogram slope > 0  (momentum naik)
      SELL → RSI slope < 0 AND MACD histogram slope < 0  (momentum turun)

    Rules:
      BUY  → H1 trend UP   + minimal 3/4 indikator M15 buy  + slope filter buy
      SELL → H1 trend DOWN + minimal 3/4 indikator M15 sell + slope filter sell
      HOLD → H1 sideways ATAU indikator tidak cukup ATAU slope berlawanan

    Risk Management (best param dari sweep):
      ATR Filter    = minimal 10 pips  (0.0010)
      Stop Loss     = 1.0 × ATR M15
      Take Profit 1 = 1.5 × ATR M15  (RR 1:1.5)
      Take Profit 2 = 2.0 × ATR M15  (RR 1:2)

    Hasil backtest sweep terbaik (2021-2026, 5,760 kombinasi):
      Win Rate     : 48.6%  (dari 35.6%)
      Total Pips   : +119   (dari -3,448)
      Profit Factor: 1.65   (dari 0.83)
      Max Drawdown : -$8.34 (modal $100, lot 0.01)
      Total Trade  : 35     (~1 trade/bulan, filter sangat ketat)
    """

    def __init__(self, symbol: str | None = None):
        self.symbol = symbol or settings.TRADING_SYMBOL

    # ─── Fetch Candle ─────────────────────────────────────────────────────────

    def _fetch_candles(self, timeframe: str, count: int) -> pd.DataFrame:
        tf = _TF_MAP.get(timeframe)
        if tf is None:
            raise ValueError(f"Timeframe '{timeframe}' tidak dikenali. Pilihan: {list(_TF_MAP.keys())}")

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

    # ─── H1: Trend Filter ─────────────────────────────────────────────────────

    def _analyze_trend_h1(self, df_h1: pd.DataFrame) -> dict:
        """
        Tentukan trend utama dari H1.
        UP       → EMA50 > EMA200
        DOWN     → EMA50 < EMA200
        SIDEWAYS → EMA50 == EMA200 (sangat jarang)
        """
        ema_50  = calculate_ema(df_h1, 50)
        ema_200 = calculate_ema(df_h1, 200)
        close   = float(df_h1["close"].iloc[-1])
        e50     = float(ema_50.iloc[-1])
        e200    = float(ema_200.iloc[-1])

        if e50 > e200:
            trend = "up"
        elif e50 < e200:
            trend = "down"
        else:
            trend = "sideways"

        return {
            "open_h1":    round(float(df_h1["open"].iloc[-1]), 4),
            "high_h1":    round(float(df_h1["high"].iloc[-1]), 4),
            "low_h1":     round(float(df_h1["low"].iloc[-1]), 4),
            "close_h1":   round(close, 4),
            "volume_h1":  round(float(df_h1["volume"].iloc[-1]), 2),
            "trend_h1":   trend,
            "ema_50_h1":  round(e50, 4),
            "ema_200_h1": round(e200, 4),
        }

    # ─── M15: Entry Trigger ───────────────────────────────────────────────────

    def _analyze_entry_m15(self, df_m15: pd.DataFrame) -> dict:
        """
        Cek 4 indikator M15 — minimal 3/4 harus searah untuk entry valid.
        """
        close = float(df_m15["close"].iloc[-1])

        # 1. RSI
        rsi     = calculate_rsi(df_m15, period=14)
        rsi_val = float(rsi.iloc[-1])
        if rsi_val <= 30:
            rsi_bias = "buy"
        elif rsi_val >= 70:
            rsi_bias = "sell"
        else:
            rsi_bias = "hold"

        # 2. MACD posisi
        macd_line, signal_line, histogram = calculate_macd(df_m15)
        curr_macd = float(macd_line.iloc[-1])
        curr_sig  = float(signal_line.iloc[-1])
        if curr_macd > curr_sig:
            macd_bias = "buy"
        elif curr_macd < curr_sig:
            macd_bias = "sell"
        else:
            macd_bias = "hold"

        # 3. EMA 9/21 posisi
        ema_9  = calculate_ema(df_m15, 9)
        ema_21 = calculate_ema(df_m15, 21)
        curr_e9  = float(ema_9.iloc[-1])
        curr_e21 = float(ema_21.iloc[-1])
        if curr_e9 > curr_e21:
            ema_bias = "buy"
        elif curr_e9 < curr_e21:
            ema_bias = "sell"
        else:
            ema_bias = "hold"

        # 4. Bollinger Bands
        bb_upper, bb_mid, bb_lower = calculate_bbands(df_m15)
        bb_upper_val = float(bb_upper.iloc[-1])
        bb_mid_val   = float(bb_mid.iloc[-1])
        bb_lower_val = float(bb_lower.iloc[-1])
        if close < bb_mid_val:
            bb_bias = "buy"
        elif close > bb_mid_val:
            bb_bias = "sell"
        else:
            bb_bias = "hold"

        # ATR untuk SL/TP
        atr     = calculate_atr(df_m15, period=14)
        atr_val = float(atr.iloc[-1])

        # Slope filter — RSI slope & MACD histogram slope (pakai 2 candle terakhir)
        rsi_slope  = float(rsi.iloc[-1])  - float(rsi.iloc[-2])
        macd_slope = float(histogram.iloc[-1]) - float(histogram.iloc[-2])

        return {
            "open_m15":           round(float(df_m15["open"].iloc[-1]), 4),
            "high_m15":           round(float(df_m15["high"].iloc[-1]), 4),
            "low_m15":            round(float(df_m15["low"].iloc[-1]), 4),
            "close_m15":          round(close, 4),
            "volume_m15":         round(float(df_m15["volume"].iloc[-1]), 2),
            "rsi_m15":            round(rsi_val, 4),
            "rsi_bias":           rsi_bias,
            "rsi_slope":          round(rsi_slope, 4),
            "macd_m15":           round(curr_macd, 4),
            "macd_signal_m15":    round(curr_sig, 4),
            "macd_histogram_m15": round(float(histogram.iloc[-1]), 4),
            "macd_bias":          macd_bias,
            "macd_slope":         round(macd_slope, 6),
            "ema_9_m15":          round(curr_e9, 4),
            "ema_21_m15":         round(curr_e21, 4),
            "ema_bias":           ema_bias,
            "bb_upper_m15":       round(bb_upper_val, 4),
            "bb_middle_m15":      round(bb_mid_val, 4),
            "bb_lower_m15":       round(bb_lower_val, 4),
            "bb_bias":            bb_bias,
            "atr_m15":            round(atr_val, 4),
        }

    # ─── Main ─────────────────────────────────────────────────────────────────

    def _fetch_all_candles(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        if not mt5.initialize(path=settings.MT5_PATH):
            raise RuntimeError(f"MT5 initialize gagal di executor thread: {mt5.last_error()}")
        try:
            df_h1  = self._fetch_candles("1h",  count=250)
            df_m15 = self._fetch_candles("15m", count=100)
            return df_h1, df_m15
        finally:
            mt5.shutdown()

    async def get_signal(self, db: AsyncSession) -> dict:
        logger.info(f"[{self.symbol}] Menganalisa sinyal H1 (trend) + M15 (entry)")

        loop = asyncio.get_event_loop()
        df_h1, df_m15 = await loop.run_in_executor(None, self._fetch_all_candles)

        h1  = self._analyze_trend_h1(df_h1)
        m15 = self._analyze_entry_m15(df_m15)

        trend    = h1["trend_h1"]
        biases   = [m15["rsi_bias"], m15["macd_bias"], m15["ema_bias"], m15["bb_bias"]]
        buy_cnt  = biases.count("buy")
        sell_cnt = biases.count("sell")

        # Slope filter — RSI slope & MACD histogram slope harus searah
        rsi_slope  = m15["rsi_slope"]
        macd_slope = m15["macd_slope"]
        slope_buy  = rsi_slope > 0 and macd_slope > 0   # momentum naik
        slope_sell = rsi_slope < 0 and macd_slope < 0   # momentum turun

        # Filter ATR — hindari market flat/ranging (minimal 10 pips)
        atr_val = m15["atr_m15"]
        if atr_val < 0.0010:
            logger.info(f"[{self.symbol}] ATR terlalu kecil ({atr_val:.5f}) — market flat, skip signal")
            action = "hold"
        # Minimal 3/4 indikator M15 searah dengan trend H1 + slope filter
        elif trend == "up" and buy_cnt >= 3 and slope_buy:
            action = "buy"
        elif trend == "down" and sell_cnt >= 3 and slope_sell:
            action = "sell"
        else:
            action = "hold"

        # Risk Management scalping — SL/TP lebih ketat
        close = m15["close_m15"]
        atr   = m15["atr_m15"]
        if action == "buy":
            sl  = round(close - 1.0 * atr, 4)
            tp1 = round(close + 1.5 * atr, 4)
            tp2 = round(close + 2.0 * atr, 4)
        elif action == "sell":
            sl  = round(close + 1.0 * atr, 4)
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
            **m15,
        }

        logger.info(
            f"[{self.symbol}] Signal: {action.upper()} | "
            f"Trend H1: {trend} | ATR: {atr_val:.5f} | "
            f"M15 Biases → RSI={m15['rsi_bias']}({rsi_slope:+.2f}) "
            f"MACD={m15['macd_bias']}({macd_slope:+.5f}) "
            f"EMA={m15['ema_bias']} BB={m15['bb_bias']} | "
            f"Slope buy={slope_buy} sell={slope_sell} | "
            f"SL: {sl} | TP1: {tp1} | TP2: {tp2}"
        )

        repo   = TradeSignalRepository(db)
        record = await repo.save(result)
        logger.success(
            f"[{self.symbol}] Signal tersimpan | ID: {record.id} | Signal: {action.upper()}"
        )

        # Eksekusi order ke MT5 jika signal BUY atau SELL
        if action in ("buy", "sell"):
            order_result = await TradeOrderUsecase().execute(
                db=db,
                signal_id=record.id,
                action=action,
                sl=sl,
                tp=tp1,  # pakai TP1 sebagai target
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
