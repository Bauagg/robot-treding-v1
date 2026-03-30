import sys
from loguru import logger


def setup_logger():
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | <cyan>{name}:{line}</cyan> - {message}",
        level="DEBUG",
        colorize=True,
    )
    logger.add(
        "logs/trading.log",
        rotation="1 day",
        retention="7 days",
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{line} - {message}",
        enqueue=True,
        delay=True,
    )
    return logger
