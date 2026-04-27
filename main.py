import asyncio
from fastapi import FastAPI
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import MetaTrader5 as mt5
from alembic.config import Config as AlembicConfig
from alembic import command as alembic_command
from app.config.database import init_db, AsyncSessionLocal
from app.config.settings import settings

from app.utils.logger import setup_logger
from app.modules.trade_signal.usecase import TradeSignalUsecase
from app.modules.trade_signal_xauusd.usecase import TradeSignalXauusdUsecase
from app.modules.trade_signal_xauusd.models import TradeSignalXauusd  # noqa: F401
from app.services.bot_signal_telegram.telegram import fetch_all_symbols, build_market_analysis, send_telegram
from app.modules.trade_signal.models import TradeSignal       # noqa: F401
from app.modules.trade_order.models import TradeOrder         # noqa: F401
from app.modules.candle_pattern.models import CandlePattern   # noqa: F401
from app.modules.trade_order.usecase import TradeOrderUsecase
from app.services.router import register_routers

setup_logger()

scheduler = AsyncIOScheduler()

_last_candle_m15 = None
_last_candle_m5  = None


def _fetch_latest_candle_times() -> dict:
    """Fetch timestamp candle M15 (EURUSD) dan M5 (XAUUSD) sekaligus — satu sesi MT5."""
    from loguru import logger
    ok = mt5.initialize(
        path=settings.MT5_PATH,
        login=settings.MT5_LOGIN,
        password=settings.MT5_PASSWORD,
        server=settings.MT5_SERVER,
    )
    if not ok:
        err = mt5.last_error()
        logger.error(f"MT5 initialize gagal | error={err} | login={settings.MT5_LOGIN} | server={settings.MT5_SERVER}")
        return {}
    try:
        result = {}
        r_m15 = mt5.copy_rates_from_pos(settings.TRADING_SYMBOL,    mt5.TIMEFRAME_M15, 0, 1)
        r_m5  = mt5.copy_rates_from_pos(settings.XAUUSD_SYMBOL,     mt5.TIMEFRAME_M5,  0, 1)
        if r_m15 and len(r_m15) > 0:
            result["m15"] = int(r_m15[0]["time"])
        if r_m5 and len(r_m5) > 0:
            result["m5"]  = int(r_m5[0]["time"])
        return result
    finally:
        mt5.shutdown()


async def check_new_candle():
    """
    Poll tiap 1 menit — cek candle M15 (EURUSD) dan M5 (XAUUSD) sekaligus.
    Masing-masing analisa hanya jalan kalau ada candle baru di timeframe-nya.
    """
    from loguru import logger
    global _last_candle_m15, _last_candle_m5

    try:
        loop = asyncio.get_event_loop()
        times = await loop.run_in_executor(None, _fetch_latest_candle_times)

        if not times:
            logger.warning("Tidak bisa fetch candle dari MT5")
            return

        # Monitor posisi open + pending order setiap poll (kedua robot)
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await TradeOrderUsecase().monitor_open_orders(session)
                await TradeOrderUsecase().monitor_pending_orders(session)
                await TradeOrderUsecase(symbol=settings.XAUUSD_SYMBOL).monitor_open_orders(session)
                await TradeOrderUsecase(symbol=settings.XAUUSD_SYMBOL).monitor_pending_orders(session)

        # ── EURUSD M15 ──────────────────────────────────────────────────────
        m15_time = times.get("m15")
        if m15_time:
            if _last_candle_m15 is None:
                _last_candle_m15 = m15_time
                logger.info(f"[{settings.TRADING_SYMBOL}] Candle M15 pertama: {m15_time}, menunggu berikutnya...")
            elif m15_time != _last_candle_m15:
                _last_candle_m15 = m15_time
                logger.info(f"[{settings.TRADING_SYMBOL}] Candle M15 baru: {m15_time}, menjalankan analisa...")

                all_frames = await loop.run_in_executor(None, fetch_all_symbols)
                for sym, frames in all_frames.items():
                    msg = build_market_analysis(sym, frames)
                    if msg:
                        await send_telegram(msg, symbol=sym)

                async with AsyncSessionLocal() as session:
                    async with session.begin():
                        await TradeSignalUsecase().get_signal(session)

        # ── XAUUSD M5 ───────────────────────────────────────────────────────
        m5_time = times.get("m5")
        if m5_time:
            if _last_candle_m5 is None:
                _last_candle_m5 = m5_time
                logger.info(f"[{settings.XAUUSD_SYMBOL}] Candle M5 pertama: {m5_time}, menunggu berikutnya...")
            elif m5_time != _last_candle_m5:
                _last_candle_m5 = m5_time
                logger.info(f"[{settings.XAUUSD_SYMBOL}] Candle M5 baru: {m5_time}, menjalankan analisa...")

                async with AsyncSessionLocal() as session:
                    async with session.begin():
                        await TradeSignalXauusdUsecase().get_signal(session)

    except Exception as e:
        logger.error(f"Signal job error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from loguru import logger
    import asyncio

    def _run_migrations():
        alembic_cfg = AlembicConfig("alembic.ini")
        # Auto-merge kalau ada multiple heads
        from alembic.script import ScriptDirectory
        script = ScriptDirectory.from_config(alembic_cfg)
        heads = script.get_heads()
        if len(heads) > 1:
            alembic_command.merge(alembic_cfg, "heads", message="auto_merge_heads")
        alembic_command.upgrade(alembic_cfg, "heads")

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _run_migrations)
        logger.info("Database migration selesai.")
    except Exception as e:
        logger.error(f"Migration gagal: {e}")

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
