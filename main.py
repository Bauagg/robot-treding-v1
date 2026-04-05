import asyncio
from fastapi import FastAPI
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import MetaTrader5 as mt5
from app.config.database import init_db, AsyncSessionLocal
from app.config.settings import settings

from app.utils.logger import setup_logger
from app.modules.trade_signal.usecase import TradeSignalUsecase
from app.modules.trade_signal.models import TradeSignal       # noqa: F401
from app.modules.trade_order.models import TradeOrder         # noqa: F401
from app.modules.candle_pattern.models import CandlePattern   # noqa: F401
from app.modules.trade_order.usecase import TradeOrderUsecase
from app.services.router import register_routers

setup_logger()

scheduler = AsyncIOScheduler()

_last_candle_time = None


def _fetch_latest_candle_time() -> int | None:
    """Fetch timestamp candle M15 terbaru — dijalankan di executor thread."""
    from loguru import logger
    ok = mt5.initialize(
        path=settings.MT5_PATH,
        login=settings.MT5_LOGIN,
        password=settings.MT5_PASSWORD,
        server=settings.MT5_SERVER,
    )
    if not ok:
        err = mt5.last_error()
        logger.error(f"MT5 initialize gagal | error={err} | path={settings.MT5_PATH} | login={settings.MT5_LOGIN} | server={settings.MT5_SERVER}")
        return None
    try:
        rates = mt5.copy_rates_from_pos(settings.TRADING_SYMBOL, mt5.TIMEFRAME_M15, 0, 1)
        if rates is None or len(rates) == 0:
            logger.error(f"MT5 copy_rates gagal | error={mt5.last_error()} | symbol={settings.TRADING_SYMBOL}")
            return None
        return int(rates[0]["time"])
    finally:
        mt5.shutdown()


async def check_new_candle():
    """
    Poll tiap 1 menit, cek apakah candle M15 baru terbentuk dari MT5.
    Analisa hanya jalan kalau ada candle baru.
    """
    from loguru import logger
    global _last_candle_time

    try:
        loop = asyncio.get_event_loop()
        candle_time = await loop.run_in_executor(None, _fetch_latest_candle_time)

        if candle_time is None:
            logger.warning("Tidak bisa fetch candle dari MT5")
            return

        # Monitor posisi open setiap poll — update DB kalau sudah close
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await TradeOrderUsecase().monitor_open_orders(session)

        if _last_candle_time is None:
            _last_candle_time = candle_time
            logger.info(f"Candle M15 pertama terdeteksi: {candle_time}, menunggu candle berikutnya...")
            return

        if candle_time != _last_candle_time:
            _last_candle_time = candle_time
            logger.info(f"Candle M15 baru terdeteksi: {candle_time}, menjalankan analisa...")
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    await TradeSignalUsecase().get_signal(session)

    except Exception as e:
        logger.error(f"Signal job error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    scheduler.add_job(check_new_candle, "interval", minutes=1, id="signal_job")
    scheduler.start()

    yield

    scheduler.shutdown()


app = FastAPI(title="Robot Trading", lifespan=lifespan)
register_routers(app)


@app.get("/")
async def root():
    return {"status": "ok", "message": "Robot Trading is running"}


@app.get("/health")
async def health():
    return {"status": "healthy", "db": settings.DB_HOST, "db_name": settings.DB_NAME}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.APP_HOST, port=settings.APP_PORT, reload=settings.DEBUG)
