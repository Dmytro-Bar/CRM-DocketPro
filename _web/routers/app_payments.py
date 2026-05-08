"""AppPayments router — lyqpay and other payments without invoice."""

from datetime import date
from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from database import get_db
from models import fmt_money, fmt_date, parse_date, APP_PAYMENT_SOURCES

router = APIRouter(prefix="/app-payments")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["fmt_money"] = fmt_money
templates.env.globals["fmt_date"]  = fmt_date


@router.get("", response_class=HTMLResponse)
async def list_app_payments(
    request:   Request,
    date_from: str = Query(default=""),
    date_to:   str = Query(default=""),
    source:    str = Query(default=""),
):
    today = date.today()
    sql  = "SELECT * FROM app_payments WHERE 1=1"
    args = []
    if date_from:
        sql += " AND pay_date >= ?"; args.append(date_from)
    if date_to:
        sql += " AND pay_date <= ?"; args.append(date_to)
    if source:
        sql += " AND source=?"; args.append(source)
    sql += " ORDER BY pay_date DESC"

    with get_db() as db:
        rows = db.execute(sql, args).fetchall()
        total = sum(float(r["amount"] or 0) for r in rows)

    return templates.TemplateResponse("app_payments.html", {
        "request":   request,
        "payments":  rows,
        "total":     total,
        "sources":   APP_PAYMENT_SOURCES,
        "date_from": date_from,
        "date_to":   date_to,
        "source":    source,
        "today_str": fmt_date(today),
    })


@router.post("/new")
async def create_payment(
    pay_date:    str   = Form(...),
    amount:      float = Form(...),
    currency:    str   = Form("UAH"),
    description: str   = Form(""),
    source:      str   = Form("lyqpay"),
):
    with get_db() as db:
        # Deduplication by (pay_date, amount)
        exists = db.execute(
            "SELECT id FROM app_payments WHERE pay_date=? AND amount=?",
            (pay_date, amount)
        ).fetchone()
        if not exists:
            db.execute(
                "INSERT INTO app_payments (pay_date,amount,currency,description,source) "
                "VALUES (?,?,?,?,?)",
                (pay_date, amount, currency, description.strip(), source)
            )
    return RedirectResponse("/app-payments", status_code=303)


@router.post("/{payment_id}/delete")
async def delete_payment(payment_id: int):
    with get_db() as db:
        db.execute("DELETE FROM app_payments WHERE id=?", (payment_id,))
    return RedirectResponse("/app-payments", status_code=303)
