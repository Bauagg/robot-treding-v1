from datetime import date
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.trade_signal_xauusd.usecase import TradeSignalXauusdUsecase


class TradeSignalXauusdController:

    def __init__(self):
        self.usecase = TradeSignalXauusdUsecase()

    async def list_signals(
        self,
        db: AsyncSession,
        filters: list[dict] | None,
        page: int,
        page_size: int,
    ) -> dict:
        return await self.usecase.list_signals(
            db, filters=filters, page=page, page_size=page_size
        )

    async def get_signal_detail(self, db: AsyncSession, signal_id: int) -> dict:
        record = await self.usecase.get_signal_by_id(db, signal_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Signal ID {signal_id} tidak ditemukan")
        return record

    async def get_dashboard(
        self,
        db: AsyncSession,
        date_from: date,
        date_to: date,
        signal: str | None,
    ) -> dict:
        return await self.usecase.get_dashboard(
            db,
            date_from=date_from,
            date_to=date_to,
            signal=signal,
        )
