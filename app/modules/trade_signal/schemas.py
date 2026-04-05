from datetime import datetime
from pydantic import BaseModel


class TradeSignalResponse(BaseModel):
    id:      int
    symbol:  str
    signal:  str
    sl:      float | None
    tp1:     float | None
    tp2:     float | None

    # H1 candlestick
    open_h1:   float
    high_h1:   float
    low_h1:    float
    close_h1:  float
    volume_h1: float

    # H1 trend & S/R
    trend_h1:      str
    ema_50_h1:     float
    ema_200_h1:    float
    atr_h1:        float
    in_support:    bool
    in_resistance: bool

    # M15 candlestick
    open_m15:   float
    high_m15:   float
    low_m15:    float
    close_m15:  float
    volume_m15: float

    # M15 indicators
    ema_9_m15:          float
    ema_21_m15:         float
    ema_bias:           str
    macd_histogram_m15: float
    macd_slope:         float
    macd_bias:          str
    atr_m15:            float

    # Candle pattern
    has_bull_pattern: bool
    has_bear_pattern: bool

    # Confluence score
    score: int

    # Timestamps
    timestamp_h1:  datetime
    timestamp_m15: datetime
    created_at:    datetime

    model_config = {"from_attributes": True}


class TradeSignalListResponse(BaseModel):
    data:      list[TradeSignalResponse]
    total:     int
    page:      int
    page_size: int
    pages:     int


class DashboardSummary(BaseModel):
    total:      int
    total_buy:  int
    total_sell: int
    total_hold: int
    avg_atr:    float | None


class DashboardResponse(BaseModel):
    date_from: str
    date_to:   str
    summary:   DashboardSummary
    data:      list[TradeSignalResponse]
