from datetime import datetime
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.trade_order.models import TradeOrder

_ALL_COLUMNS  = set(TradeOrder.__table__.columns.keys())
_SUPPORTED_OPS = {
    "eq", "neq", "gt", "gte", "lt", "lte",
    "like", "ilike", "in", "nin", "is_null", "not_null",
}


def _apply_filter(stmt, key: str, op: str, value: Any):
    if key not in _ALL_COLUMNS:
        raise ValueError(f"Kolom '{key}' tidak ada di TradeOrder")
    if op not in _SUPPORTED_OPS:
        raise ValueError(f"Operator '{op}' tidak didukung")

    col = getattr(TradeOrder, key)
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


class TradeOrderRepository:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def save(self, data: dict) -> TradeOrder:
        record = TradeOrder(**data)
        self.db.add(record)
        await self.db.flush()
        return record

    async def get_by_id(self, order_id: int) -> TradeOrder | None:
        result = await self.db.execute(
            select(TradeOrder).where(TradeOrder.id == order_id)
        )
        return result.scalar_one_or_none()

    async def get_list(
        self,
        filters: list[dict] | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        stmt = select(TradeOrder).order_by(TradeOrder.id.desc())

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

    async def get_open_orders(self, symbol: str) -> list[TradeOrder]:
        result = await self.db.execute(
            select(TradeOrder).where(
                TradeOrder.symbol == symbol,
                TradeOrder.status == "open",
                TradeOrder.ticket.is_not(None),
            )
        )
        return result.scalars().all()

    async def close_order(self, order: TradeOrder, close_price: float, profit: float) -> TradeOrder:
        order.status      = "closed"
        order.close_price = close_price
        order.profit      = profit
        order.outcome     = "profit" if profit > 0 else "loss" if profit < 0 else "be"
        order.closed_at   = datetime.utcnow()
        await self.db.flush()
        return order
