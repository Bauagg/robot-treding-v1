from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.config.database import Base


class TradeSignal(Base):
    __tablename__ = "trade_signals"

    id:      Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol:  Mapped[str] = mapped_column(String(20))
    signal:  Mapped[str] = mapped_column(String(10))   # buy / sell / hold
    sl:      Mapped[float | None] = mapped_column(Float, nullable=True)
    tp1:     Mapped[float | None] = mapped_column(Float, nullable=True)
    # ── H1 candlestick ────────────────────────────────────────────────────
    open_h1:    Mapped[float] = mapped_column(Float)
    high_h1:    Mapped[float] = mapped_column(Float)
    low_h1:     Mapped[float] = mapped_column(Float)
    close_h1:   Mapped[float] = mapped_column(Float)
    volume_h1:  Mapped[float] = mapped_column(Float)

    # ── H1 trend & S/R ───────────────────────────────────────────────────
    trend_h1:      Mapped[str]   = mapped_column(String(10))   # up / down / sideways
    ema_50_h1:     Mapped[float] = mapped_column(Float)
    ema_200_h1:    Mapped[float] = mapped_column(Float)
    atr_h1:        Mapped[float] = mapped_column(Float)
    in_support:    Mapped[bool]  = mapped_column(Boolean, default=False)
    in_resistance: Mapped[bool]  = mapped_column(Boolean, default=False)

    # ── M15 candlestick ───────────────────────────────────────────────────
    open_m15:   Mapped[float] = mapped_column(Float)
    high_m15:   Mapped[float] = mapped_column(Float)
    low_m15:    Mapped[float] = mapped_column(Float)
    close_m15:  Mapped[float] = mapped_column(Float)
    volume_m15: Mapped[float] = mapped_column(Float)

    # ── M15 indicators (Precision Strategy) ──────────────────────────────
    ema_9_m15:          Mapped[float] = mapped_column(Float)
    ema_21_m15:         Mapped[float] = mapped_column(Float)
    ema_bias:           Mapped[str]   = mapped_column(String(10))   # buy / sell / hold
    macd_histogram_m15: Mapped[float] = mapped_column(Float)
    macd_slope:         Mapped[float] = mapped_column(Float)
    macd_bias:          Mapped[str]   = mapped_column(String(10))   # buy / sell / hold
    atr_m15:            Mapped[float] = mapped_column(Float)

    # ── Candle pattern ────────────────────────────────────────────────────
    has_bull_pattern: Mapped[bool] = mapped_column(Boolean, default=False)
    has_bear_pattern: Mapped[bool] = mapped_column(Boolean, default=False)

    # ── Confluence score ──────────────────────────────────────────────────
    score: Mapped[int] = mapped_column(Integer, default=0)   # 0–5

    # ── Timestamps ────────────────────────────────────────────────────────
    timestamp_h1:  Mapped[datetime] = mapped_column(DateTime)
    timestamp_m15: Mapped[datetime] = mapped_column(DateTime)
    created_at:    Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())
