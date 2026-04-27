from datetime import datetime
from pydantic import BaseModel, field_serializer


def _fmt(v: float | None, decimals: int) -> str | None:
    return f"{v:.{decimals}f}" if v is not None else None


class TradeOrderResponse(BaseModel):
    id:           int
    signal_id:    int
    candle_id:    int | None
    symbol:       str
    action:       str
    lot:          float
    price:        float
    sl:           float | None
    tp:           float | None
    ticket:       int | None
    status:       str
    entry_target: float | None
    expire_at:    datetime | None
    close_price:  float | None
    profit:       float | None
    outcome:      str | None
    comment:      str | None
    created_by:   str
    closed_at:    datetime | None
    created_at:   datetime

    @field_serializer("price", "sl", "tp", "close_price", "entry_target")
    def fmt_price(self, v: float | None) -> str | None:
        return _fmt(v, 5)

    @field_serializer("profit")
    def fmt_profit(self, v: float | None) -> str | None:
        return _fmt(v, 2)

    @field_serializer("lot")
    def fmt_lot(self, v: float | None) -> str | None:
        return _fmt(v, 2)

    model_config = {"from_attributes": True}


class TradeOrderListResponse(BaseModel):
    data:      list[TradeOrderResponse]
    total:     int
    page:      int
    page_size: int
    pages:     int
