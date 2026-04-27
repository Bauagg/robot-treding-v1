import json
from datetime import date
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.modules.trade_signal_xauusd.controller import TradeSignalXauusdController
from app.modules.trade_signal_xauusd.schemas import (
    TradeSignalXauusdListResponse,
    TradeSignalXauusdResponse,
    XauusdDashboardResponse,
)
from app.modules.trade_signal_xauusd.usecase import TradeSignalXauusdUsecase

router     = APIRouter(prefix="/trade-signals-xauusd", tags=["Trade Signal XAUUSD"])
controller = TradeSignalXauusdController()


@router.get("/run")
async def run_signal(db: AsyncSession = Depends(get_db)):
    """
    Jalankan analisa XAUUSD 5m sekarang — ambil candle, hitung score, order kalau >= 5.
    """
    usecase = TradeSignalXauusdUsecase()
    return await usecase.get_signal(db)


@router.get("", response_model=TradeSignalXauusdListResponse)
async def list_signals(
    db: AsyncSession = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    filters: str | None = Query(
        default=None,
        description='Filter JSON. Contoh: [{"key":"signal","op":"eq","value":"buy"}]',
    ),
):
    """List XAUUSD signal dengan pagination dan filter."""
    parsed_filters = None
    if filters:
        try:
            parsed_filters = json.loads(filters)
        except json.JSONDecodeError:
            raise HTTPException(status_code=422, detail="filters harus berupa JSON array yang valid")

    return await controller.list_signals(db, filters=parsed_filters, page=page, page_size=page_size)


@router.get("/dashboard", response_model=XauusdDashboardResponse)
async def get_dashboard(
    db: AsyncSession = Depends(get_db),
    date_from: date = Query(description="Tanggal mulai (YYYY-MM-DD)"),
    date_to:   date = Query(description="Tanggal akhir (YYYY-MM-DD)"),
    signal:    str | None = Query(default=None, description="Filter signal: buy / sell"),
):
    """Dashboard XAUUSD: ringkasan buy/sell + daftar signal dalam range tanggal."""
    return await controller.get_dashboard(db, date_from=date_from, date_to=date_to, signal=signal)


@router.get("/{signal_id}", response_model=TradeSignalXauusdResponse)
async def get_signal_detail(
    signal_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Detail XAUUSD signal by ID."""
    return await controller.get_signal_detail(db, signal_id)
