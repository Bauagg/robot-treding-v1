import asyncio
import MetaTrader5 as mt5
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.modules.trade_order.repository import TradeOrderRepository


class TradeOrderUsecase:

    def __init__(self):
        self.symbol = settings.TRADING_SYMBOL
        self.lot    = settings.LOT_SIZE

    # ─── Cek posisi terbuka di MT5 ────────────────────────────────────────────

    def _get_open_positions(self) -> list:
        """Return list posisi terbuka untuk symbol ini di MT5."""
        if not mt5.initialize(path=settings.MT5_PATH):
            return []
        try:
            positions = mt5.positions_get(symbol=self.symbol)
            return list(positions) if positions else []
        finally:
            mt5.shutdown()

    # ─── Kirim order baru ─────────────────────────────────────────────────────

    def _send_order(self, action: str, sl: float | None, tp: float | None) -> dict:
        if not mt5.initialize(path=settings.MT5_PATH):
            return {"status": "failed", "comment": f"MT5 init gagal: {mt5.last_error()}"}

        try:
            tick = mt5.symbol_info_tick(self.symbol)
            if tick is None:
                return {"status": "failed", "comment": f"Tidak bisa ambil tick {self.symbol}"}

            order_type  = mt5.ORDER_TYPE_BUY if action == "buy" else mt5.ORDER_TYPE_SELL
            fill_price  = tick.ask if action == "buy" else tick.bid

            request = {
                "action":       mt5.TRADE_ACTION_DEAL,
                "symbol":       self.symbol,
                "volume":       self.lot,
                "type":         order_type,
                "price":        fill_price,
                "sl":           sl or 0.0,
                "tp":           tp or 0.0,
                "deviation":    10,
                "magic":        234000,
                "comment":      "robot-trading",
                "type_time":    mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            result = mt5.order_send(request)

            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                retcode = result.retcode if result else "None"
                comment = result.comment if result else "No response"
                logger.error(f"Order gagal | retcode: {retcode} | {comment}")
                return {"status": "failed", "ticket": None, "price": fill_price, "comment": f"retcode={retcode} {comment}"}

            logger.success(
                f"Order sukses | {action.upper()} {self.lot} lot {self.symbol} "
                f"@ {result.price} | Ticket: {result.order}"
            )
            return {"status": "open", "ticket": result.order, "price": result.price, "comment": result.comment}

        finally:
            mt5.shutdown()

    # ─── Cek posisi yang sudah close di MT5 history ───────────────────────────

    def _check_closed_position(self, ticket: int) -> dict | None:
        """
        Cek apakah posisi dengan ticket ini sudah close di MT5 history.
        Return dict dengan close_price dan profit, atau None kalau masih open.
        """
        if not mt5.initialize(path=settings.MT5_PATH):
            return None
        try:
            # Cek apakah masih ada di posisi terbuka
            positions = mt5.positions_get(ticket=ticket)
            if positions and len(positions) > 0:
                return None  # masih open

            # Cari di history deals
            from datetime import datetime, timedelta
            date_from = datetime(2000, 1, 1)
            date_to   = datetime.now() + timedelta(days=1)
            deals = mt5.history_deals_get(date_from, date_to, position=ticket)

            if not deals or len(deals) == 0:
                return None

            # Deal terakhir = close deal
            close_deal = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT]
            if not close_deal:
                return None

            deal = close_deal[-1]
            return {"close_price": deal.price, "profit": deal.profit}

        finally:
            mt5.shutdown()

    # ─── Public: execute order ────────────────────────────────────────────────

    async def execute(
        self,
        db: AsyncSession,
        signal_id: int,
        action: str,
        sl: float | None,
        tp: float | None,
    ) -> dict:
        loop = asyncio.get_event_loop()

        # Cek posisi terbuka di MT5 dan di DB
        open_positions = await loop.run_in_executor(None, self._get_open_positions)
        if open_positions:
            logger.info(f"[{self.symbol}] Ada {len(open_positions)} posisi terbuka, skip order baru.")
            return {"status": "skipped", "comment": "posisi masih terbuka"}

        repo = TradeOrderRepository(db)
        open_orders = await repo.get_open_orders(self.symbol)
        if open_orders:
            logger.info(f"[{self.symbol}] Ada order open di DB, skip order baru.")
            return {"status": "skipped", "comment": "order open masih ada di DB"}

        # Kirim order baru
        result = await loop.run_in_executor(None, self._send_order, action, sl, tp)

        record = await repo.save({
            "signal_id": signal_id,
            "symbol":    self.symbol,
            "action":    action,
            "lot":       self.lot,
            "price":     result.get("price", 0.0),
            "sl":        sl,
            "tp":        tp,
            "ticket":    result.get("ticket"),
            "status":    result["status"],
            "comment":   result.get("comment"),
        })

        return {"order_id": record.id, **result}

    # ─── Public: monitor posisi open, update DB saat close ───────────────────

    async def monitor_open_orders(self, db: AsyncSession) -> None:
        """
        Cek semua order berstatus 'open' di DB.
        Kalau sudah close di MT5, update DB dengan profit/loss.
        """
        loop = asyncio.get_event_loop()
        repo = TradeOrderRepository(db)

        open_orders = await repo.get_open_orders(self.symbol)
        if not open_orders:
            return

        for order in open_orders:
            closed = await loop.run_in_executor(None, self._check_closed_position, order.ticket)
            if closed:
                await repo.close_order(order, closed["close_price"], closed["profit"])
                outcome = "PROFIT" if closed["profit"] > 0 else "LOSS"
                logger.success(
                    f"[{self.symbol}] Order #{order.ticket} CLOSED | "
                    f"{outcome}: {closed['profit']} | Close price: {closed['close_price']}"
                )
