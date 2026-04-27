from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.config.database import Base


class TradeOrder(Base):
    __tablename__ = "trade_orders"

    id:          Mapped[int]          = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id:   Mapped[int]          = mapped_column(Integer)
    symbol:      Mapped[str]          = mapped_column(String(20))
    action:      Mapped[str]          = mapped_column(String(10))   # buy / sell
    lot:         Mapped[float]        = mapped_column(Float)
    price:       Mapped[float]        = mapped_column(Float)        # harga open
    sl:          Mapped[float | None] = mapped_column(Float, nullable=True)
    tp:          Mapped[float | None] = mapped_column(Float, nullable=True)
    ticket:       Mapped[int | None]   = mapped_column(BigInteger, nullable=True)   # MT5 ticket
    status:       Mapped[str]          = mapped_column(String(20))   # pending / open / closed / failed / expired
    entry_target: Mapped[float | None] = mapped_column(Float, nullable=True)    # harga target entry (pending order)
    expire_at:    Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # batas waktu pending order
    close_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    profit:      Mapped[float | None] = mapped_column(Float, nullable=True)     # profit/loss
    outcome:     Mapped[str | None]   = mapped_column(String(10), nullable=True)  # profit / loss / be
    comment:     Mapped[str | None]   = mapped_column(String(100), nullable=True)
    created_by:  Mapped[str]          = mapped_column(String(100), default="robot")  # robot / A. Mambaus Sholihin
    closed_at:   Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at:  Mapped[datetime]     = mapped_column(DateTime, default=lambda: datetime.utcnow())
