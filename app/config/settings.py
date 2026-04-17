from urllib.parse import quote_plus
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    DEBUG: bool = True

    # Database
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "robot_treding"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = ""
    DATABASE_URL: str = ""

    def model_post_init(self, __context):
        object.__setattr__(
            self,
            "DATABASE_URL",
            f"postgresql+asyncpg://{quote_plus(self.DB_USER)}:{quote_plus(self.DB_PASSWORD)}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}",
        )

    # MetaTrader 5
    MT5_LOGIN: int = 0
    MT5_PASSWORD: str = ""
    MT5_SERVER: str = ""
    MT5_PATH: str = r"C:\Program Files\MetaTrader 5\terminal64.exe"

    # Telegram Bot — satu chat_id untuk semua bot (bisa sama atau beda)
    TELEGRAM_CHAT_ID: str = ""

    # Token per symbol — kosongkan kalau tidak dipakai
    TELEGRAM_TOKEN_XAUUSD: str = ""
    TELEGRAM_TOKEN_GBPUSD: str = ""
    TELEGRAM_TOKEN_USDJPY: str = ""
    TELEGRAM_TOKEN_USDCAD: str = ""
    TELEGRAM_TOKEN_AUDUSD: str = ""

    # Trading
    TRADING_SYMBOL: str = "EURUSDm"
    LOT_SIZE: float = 0.01

    # Symbol yang di-analisis Telegram (pisah koma, sesuaikan suffix broker)
    WATCH_SYMBOLS: str = "XAUUSDm,GBPUSDm,USDJPYm,USDCADm,AUDUSDm"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
