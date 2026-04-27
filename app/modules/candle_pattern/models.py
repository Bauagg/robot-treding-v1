from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.config.database import Base


class CandlePattern(Base):
    __tablename__ = "candle_patterns"

    id:        Mapped[int]        = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)   # relasi ke trade_signals
    symbol:    Mapped[str]        = mapped_column(String(20))
    timeframe: Mapped[str]      = mapped_column(String(5))
    candle_time: Mapped[datetime] = mapped_column(DateTime)

    # ── OHLCV ────────────────────────────────────────────────────────────────
    open:   Mapped[float] = mapped_column(Float)
    high:   Mapped[float] = mapped_column(Float)
    low:    Mapped[float] = mapped_column(Float)
    close:  Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)

    # ── Anatomy candle ───────────────────────────────────────────────────────
    body:         Mapped[float] = mapped_column(Float)
    upper_shadow: Mapped[float] = mapped_column(Float)
    lower_shadow: Mapped[float] = mapped_column(Float)
    candle_dir:   Mapped[str]   = mapped_column(String(10))

    # ── Pattern ──────────────────────────────────────────────────────────────
    pattern_name: Mapped[str] = mapped_column(String(30))

    # ── Konteks market saat itu ───────────────────────────────────────────────
    trend_h1:      Mapped[str]  = mapped_column(String(10))
    in_support:    Mapped[bool] = mapped_column(Boolean, default=False)
    in_resistance: Mapped[bool] = mapped_column(Boolean, default=False)
    score:         Mapped[int]  = mapped_column(Integer, default=0)

    # ── Label ML ─────────────────────────────────────────────────────────────
    outcome: Mapped[str | None] = mapped_column(String(10), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
