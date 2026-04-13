from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.trade_order.repository import TradeOrderRepository
from app.modules.trade_order.usecase import TradeOrderUsecase


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

    async def create_pending_order(
        self,
        db: AsyncSession,
        action: str,
        entry_target: float,
        sl: float,
        tp: float,
        lot: float,
        expire_hours: int,
        symbol: str | None = None,
        created_by: str = "A. Mambaus Sholihin",
    ) -> dict:
        if action not in ("buy", "sell"):
            raise HTTPException(status_code=400, detail="action harus 'buy' atau 'sell'")
        if expire_hours < 1:
            raise HTTPException(status_code=400, detail="expire_hours minimal 1 jam")
        if lot <= 0:
            raise HTTPException(status_code=400, detail="lot harus lebih dari 0")

        return await TradeOrderUsecase().create_pending_order(
            db=db,
            action=action,
            entry_target=entry_target,
            sl=sl,
            tp=tp,
            lot=lot,
            expire_hours=expire_hours,
            symbol=symbol,
            created_by=created_by,
        )

    def simulate_order(
        self,
        action: str,
        entry_price: float,
        sl: float,
        tp: float,
        lot: float,
        symbol: str | None = None,
    ) -> dict:
        if action not in ("buy", "sell"):
            raise HTTPException(status_code=400, detail="action harus 'buy' atau 'sell'")
        if sl <= 0 or tp <= 0 or entry_price <= 0:
            raise HTTPException(status_code=400, detail="entry_price, sl, tp harus lebih dari 0")
        if lot <= 0:
            raise HTTPException(status_code=400, detail="lot harus lebih dari 0")

        return TradeOrderUsecase().simulate(
            action=action,
            entry_price=entry_price,
            sl=sl,
            tp=tp,
            lot=lot,
            symbol=symbol,
        )
