import asyncio
from datetime import date
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

            if buy_score >= 4:
                action = "buy"
                score  = buy_score
            elif sell_score >= 4:
                action = "sell"
                score  = sell_score

        # ── Adaptive SL/TP berdasarkan score ──
        # Score 3 → setup lemah  → SL 1.5x, TP 1.5x  (RR 1:1)
        # Score 4 → setup bagus  → SL 1.2x, TP 2.0x  (RR 1:1.7)
        # Score 5/6 → setup kuat → SL 1.0x, TP 2.5x  (RR 1:2.5)
        close = m15["close_m15"]
        atr   = m15["atr_m15"]

        if action != "hold":
            if score >= 5:
                sl_mult, tp_mult = 1.0, 2.5
            elif score == 4:
                sl_mult, tp_mult = 1.2, 2.0
            else:
                sl_mult, tp_mult = 1.5, 1.5

            if action == "buy":
                sl  = round(close - sl_mult * atr, 4)
                tp1 = round(close + tp_mult * atr, 4)
            else:
                sl  = round(close + sl_mult * atr, 4)
                tp1 = round(close - tp_mult * atr, 4)
        else:
            sl = tp1 = None

        result = {
            "symbol":        self.symbol,
            "signal":        action,
            "sl":            sl,
            "tp1":           tp1,
            "timestamp_h1":  df_h1["time"].iloc[-1],
            "timestamp_m15": df_m15["time"].iloc[-1],
            **h1,
            **{k: v for k, v in m15.items()
               if not k.startswith("macd_up") and not k.startswith("macd_down")
               and not k.startswith("has_") and not k.startswith("near_")},
        }

        logger.info(
            f"[{self.symbol}] Signal: {action.upper()} | Score: {score}/6 | "
            f"Trend H1: {trend} | InSup: {in_sup} | InRes: {in_res} | "
            f"ATR: {atr_val:.5f} | "
            f"MACD_up={m15['macd_up']} MACD_dn={m15['macd_down']} "
            f"EMA_bias={m15['ema_bias']} Pattern_bull={m15['has_bull_pattern']} "
            f"Pattern_bear={m15['has_bear_pattern']} | "
            f"SL: {sl} | TP1: {tp1}"
        )

        repo   = TradeSignalRepository(db)
        record = await repo.save(result)
        logger.success(
            f"[{self.symbol}] Signal tersimpan | ID: {record.id} | "
            f"Signal: {action.upper()} | Score: {score}/6"
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
