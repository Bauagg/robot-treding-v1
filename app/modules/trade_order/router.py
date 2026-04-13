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


@router.post("/pending")
async def create_pending_order(
    action:       str         = Query(description="buy atau sell"),
    entry_target: float       = Query(description="Harga target entry"),
    sl:           float       = Query(description="Stop Loss"),
    tp:           float       = Query(description="Take Profit"),
    lot:          float       = Query(description="Ukuran lot"),
    expire_hours: int         = Query(default=4, ge=1, le=72, description="Expire dalam berapa jam (1-72)"),
    symbol:       str | None  = Query(default=None, description="Symbol pair (opsional, default dari env). Contoh: GBPUSDm, XAUUSDm"),
    created_by:   str         = Query(default="A. Mambaus Sholihin", description="Nama pembuat order"),
    db: AsyncSession = Depends(get_db),
):
    """
    Buat pending order — belum langsung kirim ke MT5.
    Bot akan monitor harga setiap menit, kalau harga sudah mencapai
    entry_target maka order dikirim ke MT5.
    Kalau waktu sudah melewati expire_hours maka order expired otomatis.

    `symbol` opsional — user bisa pakai pair apapun (GBPUSDm, XAUUSDm, dll).
    Kalau tidak diisi, pakai symbol default dari env (robot).

    Contoh:
    ```
    POST /api/v1/trade-orders/pending?action=buy&entry_target=1.1050&sl=1.1000&tp=1.1120&lot=0.02&expire_hours=4&symbol=GBPUSDm
    ```
    """
    return await controller.create_pending_order(
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


@router.get("/simulate")
async def simulate_order(
    action:      str        = Query(description="buy atau sell"),
    entry_price: float      = Query(description="Harga entry"),
    sl:          float      = Query(description="Stop Loss"),
    tp:          float      = Query(description="Take Profit"),
    lot:         float      = Query(default=0.02, description="Ukuran lot (default 0.02)"),
    symbol:      str | None = Query(default=None, description="Symbol pair (opsional). Contoh: GBPUSDm, XAUUSDm"),
):
    """
    Simulasi kalkulasi TP/SL — tanpa kirim order ke MT5.

    Menghitung risk, reward, pip distance, dan RR ratio
    sebelum user memutuskan buat pending order.
    User bisa pakai pair apapun.

    Contoh:
    ```
    GET /api/v1/trade-orders/simulate?action=buy&entry_price=1.1050&sl=1.1010&tp=1.1110&lot=0.02&symbol=GBPUSDm
    ```
    """
    return controller.simulate_order(
        action=action,
        entry_price=entry_price,
        sl=sl,
        tp=tp,
        lot=lot,
        symbol=symbol,
    )


@router.get("/{order_id}")
async def get_order_detail(
    order_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Detail order by ID — termasuk profit/loss setelah closed."""
    return await controller.get_order_detail(db, order_id)
