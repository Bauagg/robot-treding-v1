from datetime import date, datetime
from typing import Any
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.trade_signal.models import TradeSignal

# Semua kolom yang ada di model
_ALL_COLUMNS: set[str] = set(TradeSignal.__table__.columns.keys())

# Operator yang didukung per filter item
# Format filter: [{"key": "signal", "op": "eq", "value": "buy"}, ...]
#
# Operator:
#   eq       → kolom = value
#   neq      → kolom != value
#   gt       → kolom > value
#   gte      → kolom >= value
#   lt       → kolom < value
#   lte      → kolom <= value
#   like     → kolom LIKE %value%  (case-sensitive)
#   ilike    → kolom ILIKE %value% (case-insensitive)
#   in       → kolom IN (value)    value harus list
#   nin      → kolom NOT IN (value) value harus list
#   is_null  → kolom IS NULL       (value diabaikan)
#   not_null → kolom IS NOT NULL   (value diabaikan)

_SUPPORTED_OPS = {
    "eq", "neq", "gt", "gte", "lt", "lte",
    "like", "ilike", "in", "nin", "is_null", "not_null",
}


def _apply_filter(stmt, key: str, op: str, value: Any):
    """Tambahkan satu klausa WHERE ke stmt berdasarkan operator."""
    if key not in _ALL_COLUMNS:
        raise ValueError(f"Kolom '{key}' tidak ada di TradeSignal")
    if op not in _SUPPORTED_OPS:
        raise ValueError(f"Operator '{op}' tidak didukung. Pilihan: {sorted(_SUPPORTED_OPS)}")

    col = getattr(TradeSignal, key)

    match op:
        case "eq":       return stmt.where(col == value)
        case "neq":      return stmt.where(col != value)
        case "gt":       return stmt.where(col > value)
        case "gte":      return stmt.where(col >= value)
        case "lt":       return stmt.where(col < value)
        case "lte":      return stmt.where(col <= value)
        case "like":     return stmt.where(col.like(f"%{value}%"))
        case "ilike":    return stmt.where(col.ilike(f"%{value}%"))
        case "in":       return stmt.where(col.in_(value))
        case "nin":      return stmt.where(col.not_in(value))
        case "is_null":  return stmt.where(col.is_(None))
        case "not_null": return stmt.where(col.is_not(None))


class TradeSignalRepository:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def save(self, data: dict) -> TradeSignal:
        record = TradeSignal(**{
            k: v for k, v in data.items()
            if k in _ALL_COLUMNS
        })
        self.db.add(record)
        await self.db.flush()
        return record

    async def get_by_id(self, signal_id: int) -> TradeSignal | None:
        result = await self.db.execute(
            select(TradeSignal).where(TradeSignal.id == signal_id)
        )
        return result.scalar_one_or_none()

    async def get_list(
        self,
        filters: list[dict] | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """
        List dengan pagination + filter fleksibel berbasis operator.

        filters: list of dict, tiap item punya key, op, value
        Contoh:
          [{"key": "signal", "op": "eq", "value": "buy"}]
          [{"key": "signal", "op": "in", "value": ["buy", "sell"]},
           {"key": "rsi_h1", "op": "lte", "value": 40}]
          [{"key": "symbol", "op": "ilike", "value": "EUR"}]
          [{"key": "sl", "op": "not_null"}]
        """
        stmt = select(TradeSignal).order_by(TradeSignal.id.desc())

        if filters:
            for f in filters:
                stmt = _apply_filter(stmt, f["key"], f["op"], f.get("value"))

        # Hitung total sebelum pagination
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total: int = (await self.db.execute(count_stmt)).scalar_one()

        # Pagination
        offset = (page - 1) * page_size
        stmt = stmt.offset(offset).limit(page_size)
        rows = (await self.db.execute(stmt)).scalars().all()

        return {
            "data":      rows,
            "total":     total,
            "page":      page,
            "page_size": page_size,
            "pages":     (total + page_size - 1) // page_size,
        }

    async def get_dashboard(
        self,
        date_from: date,
        date_to: date,
        signal: str | None = None,
    ) -> dict:
        """
        Dashboard — kalkulasi semua data dalam range filter, tanpa pagination.
        date_from & date_to inklusif (00:00:00 → 23:59:59).
        """
        dt_from = datetime.combine(date_from, datetime.min.time())
        dt_to   = datetime.combine(date_to,   datetime.max.time())

        # Summary: hitung buy/sell/hold + avg ATR dalam range
        summary_stmt = select(
            func.count().label("total"),
            func.sum(case((TradeSignal.signal == "buy",  1), else_=0)).label("total_buy"),
            func.sum(case((TradeSignal.signal == "sell", 1), else_=0)).label("total_sell"),
            func.sum(case((TradeSignal.signal == "hold", 1), else_=0)).label("total_hold"),
            func.avg(TradeSignal.atr_m15).label("avg_atr"),
        ).where(
            TradeSignal.created_at >= dt_from,
            TradeSignal.created_at <= dt_to,
        )
        s = (await self.db.execute(summary_stmt)).one()

        # Ambil semua signal dalam range (filter signal jika ada)
        stmt = (
            select(TradeSignal)
            .where(TradeSignal.created_at >= dt_from)
            .where(TradeSignal.created_at <= dt_to)
            .order_by(TradeSignal.created_at.desc())
        )
        if signal:
            stmt = stmt.where(TradeSignal.signal == signal.lower())

        rows = (await self.db.execute(stmt)).scalars().all()

        return {
            "date_from": date_from.isoformat(),
            "date_to":   date_to.isoformat(),
            "summary": {
                "total":      s.total      or 0,
                "total_buy":  s.total_buy  or 0,
                "total_sell": s.total_sell or 0,
                "total_hold": s.total_hold or 0,
                "avg_atr":    float(s.avg_atr) if s.avg_atr else None,
            },
            "data": rows,
        }
