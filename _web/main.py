"""
DocketPro CRM — FastAPI Web Application
Run:  uvicorn main:app --reload --port 8000  (from _web/ directory)
"""

# Load .env FIRST — before any router imports read os.getenv()
import os
from pathlib import Path as _Path
from dotenv import load_dotenv as _load_dotenv
_env_file = _Path(__file__).resolve().parent.parent / ".env"
_load_dotenv(str(_env_file), override=True)

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

import asyncio
from database import init_db
from models import fmt_money, fmt_date, norm
from config import CONTRACTS_DIR as _CONTRACTS_DIR
import nbu_client as _nbu

def _pdf_url(abs_path: str) -> str:
    """Convert absolute pdf_path stored in DB to a /docs/... web URL."""
    if not abs_path:
        return ""
    rel = abs_path
    base = _CONTRACTS_DIR.rstrip("/\\")
    if rel.startswith(base):
        rel = rel[len(base):].lstrip("/\\")
    # Normalise Windows backslashes just in case
    rel = rel.replace("\\", "/")
    return f"/docs/{rel}"

# --- Routers ---
from routers import dashboard, clients, contracts, invoices, acts, expenses, payments, app_payments, doc_templates, lyqpay, taxes
from routers import settings_router

BASE_DIR   = Path(__file__).parent
PARENT_DIR = BASE_DIR.parent

app = FastAPI(title="DocketPro CRM")

# --- Static files ---
# Existing PDF storage (read-only serve)
contracts_dir = PARENT_DIR / "Договори"
if contracts_dir.exists():
    app.mount("/docs", StaticFiles(directory=str(contracts_dir)), name="docs")

# Web static (css overrides, icons, etc.)
static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Uploaded files (signature image, etc.)
uploads_dir = BASE_DIR / "uploads"
uploads_dir.mkdir(exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")

# --- Jinja2 templates ---
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["fmt_money"] = fmt_money
templates.env.globals["fmt_date"]  = fmt_date
templates.env.globals["norm"]      = norm
templates.env.globals["pdf_url"]   = _pdf_url

# --- Init DB on startup ---
@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(_nbu_daily_loop())


async def _nbu_daily_loop():
    """Background task: refresh NBU rate once per day (24 h cycle)."""
    while True:
        try:
            _nbu.fetch_from_api()          # force-fetch, bypass cache age
            _store_fresh_rate()
        except Exception:
            pass                           # network down — silently skip
        await asyncio.sleep(24 * 3600)    # next run in 24 h


def _store_fresh_rate():
    """Fetch and overwrite cache — always call from the background task."""
    import time
    rate, rate_date = _nbu.fetch_from_api()
    from database import get_db
    with get_db() as db:
        db.execute(
            "INSERT INTO nbu_rate_cache (fetched_ts, rate, rate_date) VALUES (?,?,?)",
            (int(time.time()), rate, rate_date)
        )

# --- Include routers ---
app.include_router(dashboard.router)
app.include_router(clients.router)
app.include_router(contracts.router)
app.include_router(invoices.router)
app.include_router(acts.router)
app.include_router(expenses.router)
app.include_router(payments.router)
app.include_router(app_payments.router)
app.include_router(lyqpay.router)
app.include_router(doc_templates.router)
app.include_router(taxes.router)
app.include_router(settings_router.router)
