from datetime import date, datetime
from typing import Any
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.trade_signal_xauusd.models import TradeSignalXauusd

_ALL_COLUMNS: set[str] = set(TradeSignalXauusd.__table__.columns.keys())

_SUPPORTED_OPS = {
    "eq", "neq", "gt", "gte", "lt", "lte",
    "like", "ilike", "in", "nin", "is_null", "not_null",
}


def _apply_filter(stmt, key: str, op: str, value: Any):
    if key not in _ALL_COLUMNS:
        raise ValueError(f"Kolom '{key}' tidak ada di TradeSignalXauusd")
    if op not in _SUPPORTED_OPS:
        raise ValueError(f"Operator '{op}' tidak didukung")

    col = getattr(TradeSignalXauusd, key)

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


class TradeSignalXauusdRepository:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def save(self, data: dict) -> TradeSignalXauusd:
        record = TradeSignalXauusd(**{
            k: v for k, v in data.items()
            if k in _ALL_COLUMNS
        })
        self.db.add(record)
        await self.db.flush()
        return record

    async def get_by_id(self, signal_id: int) -> TradeSignalXauusd | None:
        result = await self.db.execute(
            select(TradeSignalXauusd).where(TradeSignalXauusd.id == signal_id)
        )
        return result.scalar_one_or_none()

    async def get_list(
        self,
        filters: list[dict] | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        stmt = select(TradeSignalXauusd).order_by(TradeSignalXauusd.id.desc())

        if filters:
            for f in filters:
                stmt = _apply_filter(stmt, f["key"], f["op"], f.get("value"))

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total: int = (await self.db.execute(count_stmt)).scalar_one()

        offset = (page - 1) * page_size
        stmt   = stmt.offset(offset).limit(page_size)
        rows   = (await self.db.execute(stmt)).scalars().all()

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
        dt_from = datetime.combine(date_from, datetime.min.time())
        dt_to   = datetime.combine(date_to,   datetime.max.time())

        summary_stmt = select(
            func.count().label("total"),
            func.sum(case((TradeSignalXauusd.signal == "buy",  1), else_=0)).label("total_buy"),
            func.sum(case((TradeSignalXauusd.signal == "sell", 1), else_=0)).label("total_sell"),
            func.avg(TradeSignalXauusd.atr_m5).label("avg_atr"),
        ).where(
            TradeSignalXauusd.created_at >= dt_from,
            TradeSignalXauusd.created_at <= dt_to,
        )
        s = (await self.db.execute(summary_stmt)).one()

        stmt = (
            select(TradeSignalXauusd)
            .where(TradeSignalXauusd.created_at >= dt_from)
            .where(TradeSignalXauusd.created_at <= dt_to)
            .order_by(TradeSignalXauusd.created_at.desc())
        )
        if signal:
            stmt = stmt.where(TradeSignalXauusd.signal == signal.lower())

        rows = (await self.db.execute(stmt)).scalars().all()

        return {
            "date_from": date_from.isoformat(),
            "date_to":   date_to.isoformat(),
            "summary": {
                "total":      s.total     or 0,
                "total_buy":  s.total_buy or 0,
                "total_sell": s.total_sell or 0,
                "avg_atr":    float(s.avg_atr) if s.avg_atr else None,
            },
            "data": rows,
        }
