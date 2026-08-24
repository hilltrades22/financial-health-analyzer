# Financial Health Analyzer

Enter a public company's ticker and get an explainable, SEC-EDGAR-sourced
financial-health analysis: liquidity, leverage, retained earnings,
capital structure, treasury stock, lease obligations, cash generation,
and debt service - each with a plain-English explanation, formula, and
source citation.

## Architecture

```
Browser  ->  FastAPI backend  ->  SEC EDGAR (data.sec.gov)  ->  normalized data  ->  scoring engine  ->  Browser
```

The browser never calls `data.sec.gov` directly. All SEC requests are made
server-side by `backend/app/sec_client.py`, which requires a real,
identifying `SEC_USER_AGENT` (set via environment variable - never
hardcoded), per [SEC's fair-access rules](https://www.sec.gov/os/webmaster-faq#developers).

- `backend/app/sec_client.py` - ticker/CIK mapping, submissions, and XBRL company facts
- `backend/app/normalize.py` - maps raw XBRL concepts into a normalized financial model (no fabricated values; missing facts stay marked unavailable)
- `backend/app/scoring.py` - the 9-rule, 100-point explainable scoring engine
- `backend/app/story.py` - builds the plain-English "Financial Story"
- `backend/app/main.py` - FastAPI app and API routes, also serves the frontend
- `frontend/` - single-page vanilla HTML/CSS/JS UI

There is no demo-data fallback. If a required SEC fact is missing for a
company, the UI shows "Not reported / unavailable from standardized SEC
data" for that item, and the corresponding scoring rule is marked
UNAVAILABLE - it earns zero points and is excluded from the score's
points-available denominator, so missing data can never inflate or
depress the score.

## Running locally

```bash
pip install -r requirements.txt
export SEC_USER_AGENT="Your Name or App your-email@example.com"
uvicorn backend.app.main:app --reload
```

Then open http://localhost:8000

## Tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

## Deployment (Render)

This repo includes `render.yaml` for a single web service (backend
serves the frontend as static files, so only one Render service is
needed).

1. Push this repo to GitHub.
2. In Render, create a new **Blueprint** from this repository (Render
   will read `render.yaml` automatically), or create a new **Web
   Service** manually with:
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT`
   - Health check path: `/api/health`
3. Set the `SEC_USER_AGENT` environment variable in the Render
   dashboard to a real contact string, e.g.
   `"Financial Health Analyzer your-email@example.com"`. Do not commit
   this to the repo or expose it in frontend code.
4. Deploy. Once live, verify `/api/health` returns `200` and that
   analyzing a real ticker (e.g. AAPL) returns live SEC data.

## Financial rules implemented

1. **Liquidity** - Cash + Marketable Securities - Short-Term Debt - Long-Term Debt (latest quarter)
2. **Debt & Leverage** - Total Liabilities / Total Equity, target < 0.80x (treasury stock and lease liabilities shown separately, not double-counted)
3. **Preferred Stock** - flags meaningful preferred stock; unavailable data is never treated as proof of absence
4. **Retained Earnings** - positive = PASS (latest annual/10-K)
5. **Retained Earnings Growth** - latest annual vs. prior annual
6. **Treasury Stock / Buybacks** - shown, explained, never automatically scored positive
7. **Free Cash Flow** - Operating Cash Flow - CapEx (latest annual)
8. **Interest Coverage** - Operating Income / Interest Expense (latest annual)
9. **Lease Obligations** - current/long-term operating and finance lease liabilities, shown separately (already included in Total Liabilities, never added twice)

Overall score, its exact point weights, and which rules were
unavailable are all shown in the UI's "Data Sources & Calculation
Details" section.
