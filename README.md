# Robot Trading

Bot trading otomatis berbasis FastAPI + MetaTrader5 + PostgreSQL.

## Tech Stack

- **FastAPI** — REST API framework
- **MetaTrader5** — koneksi ke MT5 (fetch candle, eksekusi order)
- **SQLAlchemy + asyncpg** — async database (PostgreSQL)
- **APScheduler** — polling candle M15 setiap 1 menit
- **Alembic** — database migration

## Prerequisites

- Python 3.10+
- PostgreSQL (sudah running)
- MetaTrader5 ter-install di Windows
- MT5 account (demo atau real)

---

## Setup Pertama Kali

**1. Masuk folder project**
```bash
cd robot-treding
```

**2. Buat virtual environment**
```bash
python -m venv .venv
.venv\Scripts\activate
```

**3. Install dependencies**
```bash
pip install -r requirements.txt
```

**4. Buat file `.env`** (isi sesuai akun kamu)
```env
APP_HOST=0.0.0.0
APP_PORT=8000
DEBUG=true

DB_HOST=localhost
DB_PORT=5432
DB_NAME=robot_treding
DB_USER=postgres
DB_PASSWORD=your_password

MT5_LOGIN=123456789
MT5_PASSWORD=your_mt5_password
MT5_SERVER=Monex-Demo

TRADING_SYMBOL=EURUSD.m
INITIAL_CAPITAL=1000.0
LOT_SIZE=0.01
```

**5. Buat database PostgreSQL**
```sql
CREATE DATABASE robot_treding;
```

**6. Jalankan migration** (buat tabel di DB)
```bash
alembic upgrade head
```

---

## Cara Jalankan

**Development** (auto-reload saat file berubah):
```bash
.venv\Scripts\activate
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

**Production:**
```bash
.venv\Scripts\activate
python main.py
```

Buka **http://localhost:8000/docs** untuk Swagger UI (test semua endpoint).

---

## API Endpoints

### Trade Signal
| Method | Endpoint | Keterangan |
|--------|----------|------------|
| `GET` | `/api/v1/trade-signals/dashboard` | Dashboard signal (filter by tanggal & signal) |
| `GET` | `/api/v1/trade-signals` | List signal dengan pagination & filter |
| `GET` | `/api/v1/trade-signals/{id}` | Detail signal by ID |

**Dashboard — contoh:**
```
GET /api/v1/trade-signals/dashboard
GET /api/v1/trade-signals/dashboard?date_from=2025-04-01&date_to=2026-03-03
GET /api/v1/trade-signals/dashboard?signal=buy
GET /api/v1/trade-signals/dashboard?date_from=2025-01-01&date_to=2025-12-31&signal=sell
```
Default filter: **hari ini**. Return summary (total buy/sell/hold) + semua data dalam range.

### Trade Order
| Method | Endpoint | Keterangan |
|--------|----------|------------|
| `GET` | `/api/v1/trade-orders` | List order |
| `GET` | `/api/v1/trade-orders/{id}` | Detail order by ID |

---

## Cara Kerja Bot

```
Setiap 1 menit → cek candle M15 terbaru dari MT5
    ↓ candle baru terbentuk?
    ↓ Ya
    ↓ Analisa H1 trend (EMA 50 vs EMA 200)
    ↓ Analisa M15 entry (RSI, MACD, EMA 9/21, BB)
    ↓ Cek slope filter (RSI slope + MACD slope)
    ↓ Signal BUY / SELL / HOLD
    ↓ Simpan ke DB
    ↓ BUY/SELL → eksekusi order ke MT5
```

**Strategi (hasil backtest sweep 5,760 kombinasi — 2021-2026):**
- RSI Buy ≤ 30 | RSI Sell ≥ 70
- ATR min 10 pips (hindari market flat)
- Min 3/4 indikator searah + slope filter aktif
- SL = 1.0x ATR | TP = 1.5x ATR
- Win Rate: 48.6% | Profit Factor: 1.65

---

## Struktur Project

```
robot-treding/
├── app/
│   ├── config/
│   │   ├── database.py         # Koneksi PostgreSQL
│   │   └── settings.py         # Konfigurasi dari .env
│   ├── modules/
│   │   ├── trade_signal/       # Signal analisa + simpan ke DB
│   │   │   ├── models.py
│   │   │   ├── schemas.py
│   │   │   ├── repository.py
│   │   │   ├── usecase.py
│   │   │   ├── controller.py
│   │   │   └── router.py
│   │   └── trade_order/        # Eksekusi order ke MT5
│   ├── services/
│   │   └── router.py           # Register semua router
│   ├── ai/
│   │   └── candle_ai/          # Notebook ML (eksperimen)
│   └── utils/
│       ├── indicators.py       # EMA, RSI, MACD, ATR, BB
│       └── logger.py
├── tests/
│   └── backtest/               # Notebook backtest & sweep
├── main.py                     # Entry point + scheduler
├── requirements.txt
└── .env
```

---

## Catatan Penting

- Bot hanya jalan di **Windows** (MetaTrader5 Windows only)
- MT5 harus dalam kondisi **login & terhubung** saat bot berjalan
- Tabel DB dibuat otomatis saat app pertama kali start (`create_all`)
- Log tersimpan di folder `logs/`
