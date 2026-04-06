# Robot Trading

Bot trading otomatis berbasis FastAPI + MetaTrader5 + PostgreSQL dengan Precision Strategy.

## Tech Stack

- **FastAPI** — REST API framework
- **MetaTrader5** — koneksi ke MT5 (fetch candle, eksekusi order)
- **SQLAlchemy + asyncpg** — async database (PostgreSQL)
- **APScheduler** — polling candle M15 setiap 1 menit
- **pandas-ta** — kalkulasi indikator teknikal

## Prerequisites

- Python 3.10+
- PostgreSQL (sudah running)
- MetaTrader5 ter-install di Windows
- MT5 account (demo atau real)

---

## Setup Pertama Kali

**1. Clone / download project**
```bash
cd robot-treding
```

**2. Buat virtual environment**

Windows:
```bash
python -m venv .venv
.venv\Scripts\activate
```

Nonaktifkan:
```bash
deactivate
```

Ubuntu:
```bash
python3 -m venv .venv
source .venv/bin/activate
```

Nonaktifkan:
```bash
deactivate
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
MT5_SERVER=Exness-MT5Trial6

TRADING_SYMBOL=EURUSDm
INITIAL_CAPITAL=100.0
LOT_SIZE=0.02
```

**5. Buat database PostgreSQL**
```sql
CREATE DATABASE robot_treding;
```

**6. Jalankan bot** — tabel DB dibuat otomatis saat pertama kali start.

---

## Cara Jalankan

### Windows

**Development** (auto-reload, terminal harus terbuka):
```bash
.venv\Scripts\activate
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**Background (tanpa terminal, direkomendasikan):**
- Jalankan : double-click `start_hidden.vbs`
- Stop     : double-click `stop.bat`
- Update   : double-click `update.bat` (stop → git pull → start ulang)

**Auto-start saat Windows booting:**
1. Tekan `Win + R` → ketik `shell:startup` → Enter
2. Copy file `start_hidden.vbs` ke folder yang terbuka

---

### Ubuntu / Linux

> **Catatan:** MetaTrader5 hanya tersedia di Windows. Di Ubuntu bot tidak bisa konek ke MT5.

```bash
source .venv/bin/activate
python main.py
```

**Background:**
```bash
nohup .venv/bin/python main.py > logs/trading.log 2>&1 &
echo $! > bot.pid

# Stop
kill $(cat bot.pid)
```

---

## Cara Lihat Bot Berjalan

Buka browser: **http://localhost:8000/docs** — Swagger UI untuk test semua endpoint.

Atau cek health:
```
GET http://localhost:8000/health
```

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
GET /api/v1/trade-signals/dashboard?date_from=2026-01-01&date_to=2026-04-03
GET /api/v1/trade-signals/dashboard?signal=buy
```
Default filter: **hari ini**.

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
    ↓ Ya — jalankan Precision Strategy
    ↓
    ↓ Filter jam trading (UTC): 02,03,08,09,10,12,13,16,17
    ↓ Analisa H1 (500 candle): Trend EMA50+200 + S/R Zone
    ↓ Analisa M15: MACD histogram + EMA9/21 + Candle Pattern
    ↓ Hitung Confluence Score (0-5)
    ↓ Score >= 3 → BUY / SELL
    ↓ Simpan signal ke DB
    ↓ Simpan candle pattern ke DB (dataset ML)
    ↓ BUY/SELL → eksekusi order ke MT5
```

## Precision Strategy

| Parameter | Nilai |
|-----------|-------|
| Lot | 0.02 |
| SL | 1.0x ATR M15 |
| TP | 1.5x ATR M15 (RR 1:1.5) |
| Daily TP target | +$10 (stop trading hari itu) |
| Daily SL limit | -$5 (stop trading hari itu) |

**Confluence Score (0-5):**
- `+2` Trend H1 kuat (EMA50 slope + harga vs EMA200) + harga di S/R Zone ← wajib
- `+1` MACD histogram arah searah signal
- `+1` EMA9 vs EMA21 posisi searah signal
- `+1` Candle pattern (pin bar / engulfing)

**Hasil backtest Des 2021 – Mar 2026:**
- Win Rate: 53.2% (hanya jam terbaik)
- Profit Factor: 1.86
- Total: +$395 (modal $100, lot 0.02)
- Max Drawdown: $31.82

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
│   │   ├── trade_order/        # Eksekusi order ke MT5
│   │   └── candle_pattern/     # Log candle pattern (dataset ML)
│   ├── utils/
│   │   ├── indicators.py       # EMA, MACD, ATR, S/R, candle pattern
│   │   └── logger.py
│   └── ai/
│       └── candle_ai/          # Notebook ML (eksperimen)
├── tests/
│   └── backtest_indicator/     # Notebook backtest strategi
├── start_hidden.vbs            # Jalankan bot di background (Windows)
├── stop.bat                    # Stop bot
├── update.bat                  # Update + restart bot
├── main.py                     # Entry point + scheduler
├── requirements.txt
└── .env
```

---

## Catatan Penting

- Bot hanya jalan di **Windows** (MetaTrader5 Windows only)
- MT5 harus dalam kondisi **login & terhubung** saat bot berjalan
- Tabel DB dibuat **otomatis** saat app pertama kali start
- Log tersimpan di folder `logs/`
- Jam trading aktif (WIB): 09:00, 10:00, 15:00, 16:00, 17:00, 19:00, 20:00, 23:00, 00:00
