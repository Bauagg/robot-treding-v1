from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.candle_pattern.models import CandlePattern


class CandlePatternRepository:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def save(self, data: dict) -> CandlePattern:
        record = CandlePattern(**data)
        self.db.add(record)
        await self.db.flush()
        return record

    async def update_outcome(self, record_id: int, outcome: str) -> None:
        result = await self.db.get(CandlePattern, record_id)
        if result:
            result.outcome = outcome
            await self.db.flush()
