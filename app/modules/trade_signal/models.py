from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.config.database import Base


class TradeSignal(Base):
    __tablename__ = "trade_signals"

    id:      Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol:  Mapped[str] = mapped_column(String(20))
    signal:  Mapped[str] = mapped_column(String(10))   # buy / sell / hold
    sl:      Mapped[float | None] = mapped_column(Float, nullable=True)
    tp1:     Mapped[float | None] = mapped_column(Float, nullable=True)
    tp2:     Mapped[float | None] = mapped_column(Float, nullable=True)

    # H1 candlestick (trend filter)
    open_h1:    Mapped[float] = mapped_column(Float)
    high_h1:    Mapped[float] = mapped_column(Float)
    low_h1:     Mapped[float] = mapped_column(Float)
    close_h1:   Mapped[float] = mapped_column(Float)
    volume_h1:  Mapped[float] = mapped_column(Float)

    # H1 trend
    trend_h1:   Mapped[str]   = mapped_column(String(10))
    ema_50_h1:  Mapped[float] = mapped_column(Float)
    ema_200_h1: Mapped[float] = mapped_column(Float)

    # M15 candlestick (entry)
    open_m15:   Mapped[float] = mapped_column(Float)
    high_m15:   Mapped[float] = mapped_column(Float)
    low_m15:    Mapped[float] = mapped_column(Float)
    close_m15:  Mapped[float] = mapped_column(Float)
    volume_m15: Mapped[float] = mapped_column(Float)

    # M15 indicators
    rsi_m15:            Mapped[float] = mapped_column(Float)
    rsi_bias:           Mapped[str]   = mapped_column(String(10))
    rsi_slope:          Mapped[float] = mapped_column(Float)
    macd_m15:           Mapped[float] = mapped_column(Float)
    macd_signal_m15:    Mapped[float] = mapped_column(Float)
    macd_histogram_m15: Mapped[float] = mapped_column(Float)
    macd_bias:          Mapped[str]   = mapped_column(String(10))
    macd_slope:         Mapped[float] = mapped_column(Float)
    ema_9_m15:          Mapped[float] = mapped_column(Float)
    ema_21_m15:         Mapped[float] = mapped_column(Float)
    ema_bias:           Mapped[str]   = mapped_column(String(10))
    bb_upper_m15:       Mapped[float] = mapped_column(Float)
    bb_middle_m15:      Mapped[float] = mapped_column(Float)
    bb_lower_m15:       Mapped[float] = mapped_column(Float)
    bb_bias:            Mapped[str]   = mapped_column(String(10))
    atr_m15:            Mapped[float] = mapped_column(Float)

    # Timestamps
    timestamp_h1:  Mapped[datetime] = mapped_column(DateTime)
    timestamp_m15: Mapped[datetime] = mapped_column(DateTime)
    created_at:    Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.utcnow())
