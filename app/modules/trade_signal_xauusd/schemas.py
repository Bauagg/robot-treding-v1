from datetime import datetime
from pydantic import BaseModel, field_serializer


def _fmt(v: float | None, decimals: int) -> str | None:
    return f"{v:.{decimals}f}" if v is not None else None


class TradeSignalXauusdResponse(BaseModel):
    id:     int
    symbol: str
    signal: str
    sl:     float | None
    tp1:    float | None

    # H1 trend & S/R
    trend_h1:      str
    ema_50_h1:     float
    ema_200_h1:    float
    in_support:    bool
    in_resistance: bool
    atr_h1:        float

    # M5 candlestick
    open_m5:   float
    high_m5:   float
    low_m5:    float
    close_m5:  float
    volume_m5: float

    # M5 indicators
    ema_50_m5:  float
    ema_200_m5: float
    rsi_m5:     float
    atr_m5:     float

    # Candle pattern
    has_bull_pattern: bool
    has_bear_pattern: bool

    # Score
    score: int

    # Timestamps
    timestamp_h1: datetime
    timestamp_m5: datetime
    created_at:   datetime

    @field_serializer(
        "sl", "tp1",
        "open_m5", "high_m5", "low_m5", "close_m5",
        "ema_50_h1", "ema_200_h1", "ema_50_m5", "ema_200_m5",
    )
    def fmt_price(self, v: float | None) -> str | None:
        return _fmt(v, 2)   # XAUUSD 2 desimal

    @field_serializer("atr_h1", "atr_m5", "rsi_m5")
    def fmt_indicator(self, v: float | None) -> str | None:
        return _fmt(v, 4)

    model_config = {"from_attributes": True}


class TradeSignalXauusdListResponse(BaseModel):
    data:      list[TradeSignalXauusdResponse]
    total:     int
    page:      int
    page_size: int
    pages:     int


class XauusdDashboardSummary(BaseModel):
    total:      int
    total_buy:  int
    total_sell: int
    avg_atr:    float | None


class XauusdDashboardResponse(BaseModel):
    date_from: str
    date_to:   str
    summary:   XauusdDashboardSummary
    data:      list[TradeSignalXauusdResponse]
