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

    # Exchange
    EXCHANGE_NAME: str = "binance"
    API_KEY: str = ""
    API_SECRET: str = ""
    TESTNET: bool = True

    # Trading
    TRADING_SYMBOL: str = "EURUSD"
    INITIAL_CAPITAL: float = 1000.0
    MAX_POSITION_SIZE: float = 0.1  # 10% of capital per trade
    LOT_SIZE: float = 0.01           # ukuran lot per order

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
