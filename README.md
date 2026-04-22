# CSP Screener

A web-based Cash Secured Put screener combining Bollinger Bands, SMA-50 slope, IV percentile (HV proxy), and Black-Scholes delta to surface high-probability CSP setups in the 30–45 DTE window.

## Quick Start

### 1. Backend (FastAPI + Python)

```bash
cd backend
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate

pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Swagger UI: http://localhost:8000/docs

### 2. Frontend (React + Vite)

```bash
cd frontend
npm install
npm run dev
```

App: http://localhost:5173

## Architecture

```
backend/
├── main.py                      # FastAPI app + CORS
├── requirements.txt
├── routers/
│   └── screener.py              # POST /api/screener/csp
└── services/
    ├── data_service.py          # yfinance OHLC + risk-free rate (^IRX)
    ├── technical_service.py     # Bollinger Bands, SMA50 slope, HV percentile
    ├── greeks_service.py        # Black-Scholes put delta
    ├── options_service.py       # Options chain, strike selection, premium
    └── screener_service.py      # Per-symbol orchestration

frontend/
└── src/
    ├── App.tsx
    ├── types/screener.ts
    ├── hooks/useScreener.ts
    └── components/
        ├── SymbolInput.tsx
        ├── FilterPanel.tsx
        └── ScreenerTable.tsx
```

## API

### `POST /api/screener/csp`

**Request**
```json
{
  "symbols": ["AAPL", "MSFT", "NVDA"],
  "minDTE": 30,
  "maxDTE": 45
}
```

**Response** — `{ results: [...], errors: [...] }`

| Field | Description |
|---|---|
| `symbol` | Ticker |
| `price` | Current close price |
| `bb_upper/middle/lower` | Bollinger Bands (20, 2) |
| `sma50_slope` | (SMA50_today − SMA50_5d_ago) / 5 |
| `iv_percentile` | 30-day HV rank over 252 days (proxy) |
| `earnings_date` | Next earnings (YYYY-MM-DD) |
| `earnings_within_dte` | `true` if earnings fall within selected DTE |
| `strike` | Selected put strike (nearest ≤ BB lower) |
| `strike_is_fallback` | `true` if no strike ≤ BB lower was available |
| `delta` | Black-Scholes put delta |
| `dte` | Days to expiration |
| `expiration` | Expiration date string |
| `premium` | Mid-price (bid+ask)/2, or lastPrice |
| `collateral` | strike × 100 |
| `return_pct` | premium / collateral × 100 |
| `annualized_return` | return_pct × (365 / dte) |

## Key Approximations

- **Delta**: Computed via Black-Scholes (yfinance does not provide exchange-supplied Greeks)
- **IV Percentile**: 30-day historical volatility ranked over 252 trading days (proxy for true IV percentile); labeled "IV% (HV Proxy)" in UI
- **Risk-free rate**: 13-week T-bill (^IRX), fallback 4.5%

## Constraints

- Max 20 symbols per request
- DTE range: 1–90 days
- 0.5s delay between yfinance calls to respect rate limits
- Symbols with no options in the requested DTE range appear in the `errors` list
