from fastapi import FastAPI

from app.modules.trade_signal.router import router as trade_signal_router
from app.modules.trade_order.router import router as trade_order_router


def register_routers(app: FastAPI) -> None:
    """Daftarkan semua router modul ke app dengan prefix /api/v1."""
    prefix = "/api/v1"

    app.include_router(trade_signal_router, prefix=prefix)
    app.include_router(trade_order_router, prefix=prefix)
