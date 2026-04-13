import asyncio
from datetime import datetime, timedelta, timezone
import MetaTrader5 as mt5
import pandas as pd
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.modules.trade_order.repository import TradeOrderRepository
from app.utils.analysis import analyze_h1, analyze_m15, get_confluence_score


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
        created_by: str = "robot",
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
            "signal_id":    signal_id,
            "symbol":       self.symbol,
            "action":       action,
            "lot":          self.lot,
            "price":        result.get("price", 0.0),
            "sl":           sl,
            "tp":           tp,
            "ticket":       result.get("ticket"),
            "status":       result["status"],
            "comment":      result.get("comment"),
            "created_by":   created_by,
            "entry_target": None,   # order langsung, tidak pakai target harga
            "expire_at":    None,   # order langsung, tidak ada expire
        })

        return {"order_id": record.id, **result}

    # ─── Public: buat pending order dari user ────────────────────────────────

    async def create_pending_order(
        self,
        db: AsyncSession,
        action: str,
        entry_target: float,
        sl: float,
        tp: float,
        lot: float,
        expire_hours: int,
        symbol: str | None = None,
        created_by: str = "A. Mambaus Sholihin",
    ) -> dict:
        """
        Simpan pending order ke DB — belum kirim ke MT5.
        Job monitor akan cek setiap menit, kalau harga >= entry_target
        dan belum expire maka order dikirim ke MT5.

        symbol: opsional — kalau tidak diisi pakai TRADING_SYMBOL dari env (robot).
                User bisa isi symbol apapun (GBPUSD, XAUUSD, dll).
        """
        sym       = symbol or self.symbol
        repo      = TradeOrderRepository(db)
        expire_at = datetime.now(timezone.utc) + timedelta(hours=expire_hours)

        record = await repo.save({
            "signal_id":    0,
            "symbol":       sym,
            "action":       action,
            "lot":          lot,
            "price":        0.0,          # diisi saat order tereksekusi
            "sl":           sl,
            "tp":           tp,
            "entry_target": entry_target,
            "expire_at":    expire_at,
            "status":       "pending",
            "created_by":   created_by,
            "comment":      f"Pending order | target={entry_target} | expire={expire_at.strftime('%Y-%m-%d %H:%M')} UTC",
        })

        logger.info(
            f"[{sym}] Pending order dibuat | {action.upper()} | "
            f"Target: {entry_target} | SL: {sl} | TP: {tp} | Lot: {lot} | "
            f"Expire: {expire_at.strftime('%Y-%m-%d %H:%M')} UTC | By: {created_by}"
        )
        return {"order_id": record.id, "symbol": sym, "status": "pending", "expire_at": expire_at.isoformat()}

    # ─── Public: monitor pending order, eksekusi kalau harga tercapai ────────

    async def monitor_pending_orders(self, db: AsyncSession) -> None:
        """
        Cek semua pending order:
        - Kalau sudah expire → tandai expired
        - Kalau harga sekarang >= entry_target (buy) atau <= entry_target (sell):
            - Validasi signal saat ini sesuai arah order (confluence >= 3)
            - Kalau tidak sesuai → cancel order
            - Kalau sesuai → kirim order ke MT5
        """
        loop = asyncio.get_event_loop()
        repo = TradeOrderRepository(db)

        pending = await repo.get_pending_orders(self.symbol)
        if not pending:
            return

        # Ambil harga + candle data dari MT5 sekaligus
        def _get_market_data() -> dict | None:
            ok = mt5.initialize(
                path=settings.MT5_PATH,
                login=settings.MT5_LOGIN,
                password=settings.MT5_PASSWORD,
                server=settings.MT5_SERVER,
            )
            if not ok:
                return None
            try:
                tick = mt5.symbol_info_tick(self.symbol)
                if tick is None:
                    return None

                rates_h1  = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_H1,  0, 520)
                rates_m15 = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_M15, 0, 100)
                if rates_h1 is None or rates_m15 is None:
                    return None

                df_h1  = pd.DataFrame(rates_h1)
                df_m15 = pd.DataFrame(rates_m15)
                df_h1["time"]  = pd.to_datetime(df_h1["time"],  unit="s")
                df_m15["time"] = pd.to_datetime(df_m15["time"], unit="s")
                df_h1.rename( columns={"tick_volume": "volume"}, inplace=True)
                df_m15.rename(columns={"tick_volume": "volume"}, inplace=True)

                return {"price": tick.ask, "df_h1": df_h1, "df_m15": df_m15}
            finally:
                mt5.shutdown()

        now         = datetime.now(timezone.utc)
        market_data = await loop.run_in_executor(None, _get_market_data)
        if market_data is None:
            logger.warning(f"[{self.symbol}] Tidak bisa ambil data pasar untuk cek pending order")
            return

        curr_price = market_data["price"]

        # Hitung signal satu kali untuk semua pending order
        h1        = analyze_h1(market_data["df_h1"])
        sup_zones = h1.pop("_sup_zones")
        res_zones = h1.pop("_res_zones")
        m15       = analyze_m15(market_data["df_m15"], sup_zones, res_zones)

        for order in pending:
            # Cek expire
            if order.expire_at and now >= order.expire_at:
                await repo.expire_order(order)
                logger.info(f"[{self.symbol}] Pending order #{order.id} EXPIRED | target={order.entry_target}")
                continue

            # Cek apakah harga sudah mencapai target
            target    = order.entry_target
            triggered = False
            if order.action == "buy"  and curr_price >= target:
                triggered = True
            elif order.action == "sell" and curr_price <= target:
                triggered = True

            if not triggered:
                continue

            # Validasi signal — confluence >= 3 sesuai arah order
            score = get_confluence_score(order.action, h1, m15)
            if score < 3:
                order.status  = "cancelled"
                order.comment = (
                    f"Cancelled: signal tidak sesuai saat harga={curr_price} | "
                    f"action={order.action} | score={score}/5 | "
                    f"trend={h1['trend_h1']} in_sup={h1['in_support']} in_res={h1['in_resistance']}"
                )
                await db.flush()
                logger.warning(
                    f"[{self.symbol}] Pending order #{order.id} CANCELLED — "
                    f"signal tidak valid | {order.action.upper()} | score={score}/5 | "
                    f"trend={h1['trend_h1']}"
                )
                continue

            result = await loop.run_in_executor(
                None, self._send_order, order.action, order.sl, order.tp
            )
            order.status  = result["status"]
            order.price   = result.get("price", 0.0)
            order.ticket  = result.get("ticket")
            order.comment = (
                f"Triggered at {curr_price} | score={score}/5 | {result.get('comment','')}"
            )
            await db.flush()
            logger.success(
                f"[{self.symbol}] Pending order #{order.id} TRIGGERED | "
                f"{order.action.upper()} @ {curr_price} | Score: {score}/5 | Ticket: {order.ticket}"
            )

    # ─── Public: simulasi kalkulasi TP/SL ────────────────────────────────────

    def simulate(
        self,
        action: str,
        entry_price: float,
        sl: float,
        tp: float,
        lot: float,
        symbol: str | None = None,
    ) -> dict:
        """
        Kalkulasi simulasi risk/reward sebelum order dikirim.

        Menghitung:
        - Risk (loss kalau SL kena) dalam USD
        - Reward (profit kalau TP kena) dalam USD
        - Risk/Reward ratio
        - Pip distance SL dan TP
        - Apakah RR minimal 1:1.5 (layak trading)

        symbol: opsional — untuk info saja, tidak mempengaruhi kalkulasi pip.
        Pip value: 1 pip = $0.10 per 0.01 lot (berlaku untuk pair xxx/USD & XAU/USD pakai pip=0.1)
        """
        if action not in ("buy", "sell"):
            raise ValueError("action harus 'buy' atau 'sell'")

        pip = 0.0001  # 1 pip untuk pair 5-digit

        if action == "buy":
            sl_pips  = round((entry_price - sl) / pip, 1)
            tp_pips  = round((tp - entry_price) / pip, 1)
        else:
            sl_pips  = round((sl - entry_price) / pip, 1)
            tp_pips  = round((entry_price - tp) / pip, 1)

        pip_value   = round(lot * 10, 4)          # USD per pip
        risk_usd    = round(sl_pips * pip_value, 2)
        reward_usd  = round(tp_pips * pip_value, 2)
        rr_ratio    = round(tp_pips / sl_pips, 2) if sl_pips > 0 else 0
        is_valid_rr = rr_ratio >= 1.5

        return {
            "symbol":        symbol or self.symbol,
            "action":        action,
            "entry_price":   entry_price,
            "sl":            sl,
            "tp":            tp,
            "lot":           lot,
            "sl_pips":       sl_pips,
            "tp_pips":       tp_pips,
            "pip_value_usd": pip_value,
            "risk_usd":      risk_usd,
            "reward_usd":    reward_usd,
            "rr_ratio":      rr_ratio,
            "is_valid_rr":   is_valid_rr,
            "note":          "RR layak (>= 1:1.5)" if is_valid_rr else "RR terlalu kecil (< 1:1.5), pertimbangkan ulang",
        }

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
