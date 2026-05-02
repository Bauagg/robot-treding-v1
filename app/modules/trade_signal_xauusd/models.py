from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.config.database import Base


class TradeSignalXauusd(Base):
    __tablename__ = "trade_signals_xauusd"

    id:     Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20))
    signal: Mapped[str] = mapped_column(String(10))   # buy / sell / hold
    sl:     Mapped[float | None] = mapped_column(Float, nullable=True)
    tp1:    Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── H1 trend & S/R ──────────────────────────────────────────────────────
    trend_h1:      Mapped[str]   = mapped_column(String(10))
    ema_50_h1:     Mapped[float] = mapped_column(Float)
    ema_200_h1:    Mapped[float] = mapped_column(Float)
    in_support:    Mapped[bool]  = mapped_column(Boolean, default=False)
    in_resistance: Mapped[bool]  = mapped_column(Boolean, default=False)
    atr_h1:        Mapped[float] = mapped_column(Float)

    # ── M5 candlestick ───────────────────────────────────────────────────────
    open_m5:   Mapped[float] = mapped_column(Float)
    high_m5:   Mapped[float] = mapped_column(Float)
    low_m5:    Mapped[float] = mapped_column(Float)
    close_m5:  Mapped[float] = mapped_column(Float)
    volume_m5: Mapped[float] = mapped_column(Float)

    # ── M5 indicators ────────────────────────────────────────────────────────
    ema_50_m5:  Mapped[float] = mapped_column(Float)
    ema_200_m5: Mapped[float] = mapped_column(Float)
    rsi_m5:     Mapped[float] = mapped_column(Float)
    atr_m5:     Mapped[float] = mapped_column(Float)

    # ── M5 additional indicators ─────────────────────────────────────────────
    bbw: Mapped[float | None] = mapped_column(Float, nullable=True)  # BB Width %

    # ── Candle pattern ───────────────────────────────────────────────────────
    has_bull_pattern: Mapped[bool] = mapped_column(Boolean, default=False)
    has_bear_pattern: Mapped[bool] = mapped_column(Boolean, default=False)

    # ── H1 momentum filter ───────────────────────────────────────────────────
    rsi_h1:  Mapped[float | None] = mapped_column(Float, nullable=True)  # RSI 14 H1
    adx_h1:  Mapped[float | None] = mapped_column(Float, nullable=True)  # ADX 14 H1

    # ── Confluence score ─────────────────────────────────────────────────────
    score:   Mapped[int] = mapped_column(Integer, default=0)   # 0–6
    jam_utc: Mapped[int | None] = mapped_column(Integer, nullable=True)  # jam entry UTC

    # ── Timestamps ──────────────────────────────────────────────────────────
    timestamp_h1: Mapped[datetime] = mapped_column(DateTime)
    timestamp_m5: Mapped[datetime] = mapped_column(DateTime)
    created_at:   Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
