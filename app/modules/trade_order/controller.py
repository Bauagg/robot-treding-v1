from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.trade_order.repository import TradeOrderRepository


class TradeOrderController:

    def __init__(self):
        self.repo_class = TradeOrderRepository

    async def list_orders(
        self,
        db: AsyncSession,
        filters: list[dict] | None,
        page: int,
        page_size: int,
    ) -> dict:
        return await self.repo_class(db).get_list(filters=filters, page=page, page_size=page_size)

    async def get_order_detail(self, db: AsyncSession, order_id: int) -> dict:
        record = await self.repo_class(db).get_by_id(order_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Order ID {order_id} tidak ditemukan")
        return record
