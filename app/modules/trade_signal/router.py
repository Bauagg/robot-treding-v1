import json
from datetime import date, timezone, datetime
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.modules.trade_signal.controller import TradeSignalController
from app.modules.trade_signal.schemas import TradeSignalListResponse, TradeSignalResponse, DashboardResponse

router = APIRouter(prefix="/trade-signals", tags=["Trade Signal"])

controller = TradeSignalController()


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("", response_model=TradeSignalListResponse)
async def list_signals(
    db: AsyncSession = Depends(get_db),
    page: int = Query(default=1, ge=1, description="Halaman"),
    page_size: int = Query(default=20, ge=1, le=100, description="Jumlah per halaman"),
    filters: str | None = Query(
        default=None,
        description=(
            "Filter JSON (URL-encoded). Contoh: "
            '[{"key":"signal","op":"eq","value":"buy"},'
            '{"key":"rsi_h1","op":"lte","value":40}]'
        ),
    ),
):
    """
    List trade signal dengan pagination dan filter via query params.

    **Cara pakai filter:**
    Kirim query param `filters` berisi JSON array. Tiap item punya `key`, `op`, dan `value`.

    Contoh URL:
    ```
    GET /api/v1/trade-signals?page=1&page_size=20&filters=[{"key":"signal","op":"eq","value":"buy"}]
    GET /api/v1/trade-signals?filters=[{"key":"signal","op":"in","value":["buy","sell"]},{"key":"trend_h4","op":"eq","value":"up"}]
    GET /api/v1/trade-signals?filters=[{"key":"sl","op":"not_null"}]
    ```

    **Operator:** `eq` `neq` `gt` `gte` `lt` `lte` `like` `ilike` `in` `nin` `is_null` `not_null`
    """
    parsed_filters = None
    if filters:
        try:
            parsed_filters = json.loads(filters)
        except json.JSONDecodeError:
            from fastapi import HTTPException
            raise HTTPException(status_code=422, detail="filters harus berupa JSON array yang valid")

    return await controller.list_signals(
        db, filters=parsed_filters, page=page, page_size=page_size
    )


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    db: AsyncSession = Depends(get_db),
    date_from: date | None = Query(default=None, description="Tanggal mulai (YYYY-MM-DD). Default: hari ini"),
    date_to:   date | None = Query(default=None, description="Tanggal akhir (YYYY-MM-DD). Default: hari ini"),
    signal:    str  | None = Query(default=None, description="Filter signal: buy / sell / hold"),
):
    """
    Dashboard signal — kalkulasi semua data dalam range, tanpa pagination.
    Default filter: hari ini.

    Contoh:
    ```
    GET /api/v1/trade-signals/dashboard
    GET /api/v1/trade-signals/dashboard?date_from=2025-04-01&date_to=2026-03-03
    GET /api/v1/trade-signals/dashboard?date_from=2025-01-01&date_to=2025-12-31&signal=buy
    ```
    """
    today = datetime.now(timezone.utc).date()
    return await controller.get_dashboard(
        db,
        date_from=date_from or today,
        date_to=date_to     or today,
        signal=signal,
    )


@router.get("/{signal_id}", response_model=TradeSignalResponse)
async def get_signal_detail(
    signal_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Detail trade signal by ID."""
    return await controller.get_signal_detail(db, signal_id)
