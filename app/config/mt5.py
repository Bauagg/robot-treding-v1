import MetaTrader5 as mt5
from loguru import logger

from app.config.settings import settings


def mt5_connect() -> bool:
    logger.info(f"Connecting to MT5 server: {settings.MT5_SERVER} (account: {settings.MT5_LOGIN})")
    if not mt5.initialize(path=settings.MT5_PATH):
        logger.error(f"MT5 initialize failed: {mt5.last_error()}")
        return False

    authorized = mt5.login(
        login=settings.MT5_LOGIN,
        password=settings.MT5_PASSWORD,
        server=settings.MT5_SERVER,
    )

    if not authorized:
        logger.error(f"MT5 login failed: {mt5.last_error()}")
        mt5.shutdown()
        return False

    info = mt5.account_info()
    logger.success(
        f"MT5 connected! "
        f"Account: {info.login} | "
        f"Balance: {info.balance} {info.currency} | "
        f"Server: {info.server} | "
        f"Leverage: 1:{info.leverage}"
    )
    return True


def mt5_disconnect():
    mt5.shutdown()
    logger.info("MT5 disconnected.")
