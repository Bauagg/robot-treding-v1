import json
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.modules.trade_order.controller import TradeOrderController

router = APIRouter(prefix="/trade-orders", tags=["Trade Order"])

controller = TradeOrderController()


@router.get("")
async def list_orders(
    db: AsyncSession = Depends(get_db),
    page: int = Query(default=1, ge=1, description="Halaman"),
    page_size: int = Query(default=20, ge=1, le=100, description="Jumlah per halaman"),
    filters: str | None = Query(
        default=None,
        description=(
            "Filter JSON. Contoh: "
            '[{"key":"status","op":"eq","value":"closed"},'
            '{"key":"outcome","op":"eq","value":"profit"}]'
        ),
    ),
):
    """
    List order dengan pagination dan filter.

    Contoh filter:
    ```
    GET /api/v1/trade-orders?filters=[{"key":"status","op":"eq","value":"open"}]
    GET /api/v1/trade-orders?filters=[{"key":"outcome","op":"eq","value":"profit"}]
    GET /api/v1/trade-orders?filters=[{"key":"profit","op":"gt","value":0}]
    ```
    """
    parsed_filters = None
    if filters:
        try:
            parsed_filters = json.loads(filters)
        except json.JSONDecodeError:
            raise HTTPException(status_code=422, detail="filters harus berupa JSON array yang valid")

    return await controller.list_orders(db, filters=parsed_filters, page=page, page_size=page_size)


@router.get("/{order_id}")
async def get_order_detail(
    order_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Detail order by ID — termasuk profit/loss setelah closed."""
    return await controller.get_order_detail(db, order_id)
