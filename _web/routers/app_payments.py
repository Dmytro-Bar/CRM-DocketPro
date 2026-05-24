"""All-payments router — unified view of LiqPay, manual, and bank (invoice) payments."""

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
async def list_all_payments(
    request:    Request,
    date_from:  str = Query(default=""),
    date_to:    str = Query(default=""),
    source:     str = Query(default=""),
    client:     str = Query(default=""),
    q:          str = Query(default=""),
):
    today = date.today()
    df = parse_date(date_from) if date_from else None
    dt = parse_date(date_to)   if date_to   else None

    with get_db() as db:
        ap_rows = db.execute("SELECT * FROM app_payments").fetchall()
        inv_rows = db.execute(
            "SELECT invoice_no, client_name, sum_uah, pay_date "
            "FROM invoices WHERE pay_status='Оплачено' "
            "AND pay_date IS NOT NULL AND pay_date != ''"
        ).fetchall()
        client_rows = db.execute(
            "SELECT DISTINCT client_name FROM invoices "
            "WHERE pay_status='Оплачено' ORDER BY client_name"
        ).fetchall()

    payments = []

    # ── Manual / LiqPay payments ────────────────────────────────
    if not source or source != "bank":
        for r in ap_rows:
            src = r["source"] or "manual"
            if source and source != src:
                continue
            # Hide manual entries when client filter is active (no client stored)
            if client:
                continue
            pd = parse_date(r["pay_date"])
            if df and pd and pd < df:
                continue
            if dt and pd and pd > dt:
                continue
            desc = r["description"] or ""
            if q and q.lower() not in desc.lower():
                continue
            payments.append({
                "date":        pd,
                "date_str":    r["pay_date"] or "",
                "amount":      float(r["amount"] or 0),
                "currency":    r["currency"] or "UAH",
                "client":      "",
                "description": desc,
                "source":      src,
                "invoice_no":  "",
                "row_type":    "manual",
                "record_id":   r["id"],
            })

    # ── Bank / invoice payments ─────────────────────────────────
    if not source or source == "bank":
        for r in inv_rows:
            pd = parse_date(r["pay_date"])
            if df and pd and pd < df:
                continue
            if dt and pd and pd > dt:
                continue
            cname = r["client_name"] or ""
            if client and client.lower() not in cname.lower():
                continue
            inv_no = r["invoice_no"] or ""
            if q and q.lower() not in cname.lower() and q.lower() not in inv_no.lower():
                continue
            payments.append({
                "date":        pd,
                "date_str":    r["pay_date"] or "",
                "amount":      float(r["sum_uah"] or 0),
                "currency":    "UAH",
                "client":      cname,
                "description": inv_no,
                "source":      "bank",
                "invoice_no":  inv_no,
                "row_type":    "invoice",
                "record_id":   None,
            })

    payments.sort(key=lambda p: p["date"] or date.min, reverse=True)

    total        = sum(p["amount"] for p in payments)
    total_bank   = sum(p["amount"] for p in payments if p["source"] == "bank")
    total_lyqpay = sum(p["amount"] for p in payments if p["source"] == "lyqpay")
    total_other  = total - total_bank - total_lyqpay

    return templates.TemplateResponse("app_payments.html", {
        "request":      request,
        "payments":     payments,
        "total":        total,
        "total_bank":   total_bank,
        "total_lyqpay": total_lyqpay,
        "total_other":  total_other,
        "sources":      APP_PAYMENT_SOURCES,
        "clients":      [c["client_name"] for c in client_rows],
        "date_from":    date_from,
        "date_to":      date_to,
        "source":       source,
        "client":       client,
        "q":            q,
        "today_str":    fmt_date(today),
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
