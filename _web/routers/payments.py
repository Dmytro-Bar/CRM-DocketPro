"""Payments router — Monobank API bank reconciliation."""

from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from database import get_db
from models import fmt_money, fmt_date, parse_date

import mono_client as mc

router = APIRouter(prefix="/payments")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["fmt_money"] = fmt_money
templates.env.globals["fmt_date"]  = fmt_date


def _load_unpaid_invoices(db) -> list[dict]:
    rows = db.execute(
        """SELECT i.invoice_no, i.client_name, i.sum_uah, i.currency,
                  c.edrpou
           FROM invoices i
           LEFT JOIN contracts ct ON ct.contract_no = i.contract_no
           LEFT JOIN clients   c  ON c.edrpou        = ct.edrpou
           WHERE i.pay_status NOT IN ('Оплачено','Скасовано')
             AND i.pay_status IS NOT NULL"""
    ).fetchall()
    return [dict(r) for r in rows]


def _save_lyqpay(payments: list, db) -> int:
    """Auto-save lyqpay transactions to app_payments with deduplication."""
    saved = 0
    for pay in payments:
        purpose_lower = pay.get("purpose", "").lower()
        if "lyqpay" not in purpose_lower and "liqpay" not in purpose_lower:
            continue
        pay_date_str = pay["pay_date"].strftime("%d.%m.%Y") if pay["pay_date"] else ""
        exists = db.execute(
            "SELECT id FROM app_payments WHERE pay_date=? AND amount=?",
            (pay_date_str, pay["amount"])
        ).fetchone()
        if not exists:
            db.execute(
                "INSERT INTO app_payments (pay_date, amount, currency, description, source) "
                "VALUES (?, ?, 'UAH', ?, 'lyqpay')",
                (pay_date_str, pay["amount"], pay.get("purpose", "")[:120])
            )
            saved += 1
    return saved


# ── Routes ────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
async def payments_page(request: Request):
    return templates.TemplateResponse("payments.html", {
        "request":    request,
        "results":    [],
        "stats":      {},
        "days":       30,
        "date_from":  "",
        "date_to":    "",
        "message":    "",
        "error":      "",
        "token_ok":   bool(mc.get_token()),
    })


@router.post("/fetch", response_class=HTMLResponse)
async def fetch_from_api(
    request: Request,
    days: int = Form(30),
):
    token = mc.get_token()
    iban  = mc.get_iban()

    if not token:
        return templates.TemplateResponse("payments.html", {
            "request":  request,
            "results":  [],
            "stats":    {},
            "days":     days,
            "date_from": "",
            "date_to":   "",
            "message":  "",
            "error":    "MONO_TOKEN не налаштовано. Додайте його у файл .env",
            "token_ok": False,
        })

    try:
        date_to   = date.today()
        date_from = date_to - timedelta(days=days)

        client = mc.MonoClient(token, iban)
        raw    = client.get_transactions(date_from, date_to)

        payments = mc.parse_transactions(raw)
        payments = mc.enrich_with_invoice_nos(payments)

        with get_db() as db:
            invoices = _load_unpaid_invoices(db)
            lyqpay_saved = _save_lyqpay(payments, db)

        results = mc.match_payments(payments, invoices)

        # Enrich each result row with display values
        display = []
        for res in results:
            pay     = res["payment"]
            matches = res["matches"]

            pay_date_str = pay["pay_date"].strftime("%d.%m.%Y") if pay["pay_date"] else "—"
            _p = pay.get("purpose", "").lower()
            is_lyqpay    = "lyqpay" in _p or "liqpay" in _p

            if not matches:
                tag   = "lyqpay"   if is_lyqpay else "none"
                label = "📱 lyqpay" if is_lyqpay else "Немає збігу"
                display.append({
                    "pay_date":    pay_date_str,
                    "payer":       mc.shorten_name(pay["payer"]),
                    "edrpou":      pay.get("edrpou") or "—",
                    "amount":      pay["amount"],
                    "invoice_no":  "",
                    "inv_sum":     None,
                    "match_type":  tag,
                    "match_label": label,
                    "purpose":     pay.get("purpose", ""),
                    "selectable":  False,
                    "row_pay_date": pay_date_str,
                })
            else:
                for m in matches:
                    inv        = m["invoice"]
                    mtype      = m["match_type"]
                    label_map  = {
                        "exact":  "✓ Точний",
                        "fuzzy":  f"~ Схожий (±{m.get('edit_distance', '')})",
                        "edrpou": "? ЄДРПОУ",
                    }
                    display.append({
                        "pay_date":    pay_date_str,
                        "payer":       mc.shorten_name(pay["payer"]),
                        "edrpou":      pay.get("edrpou") or "—",
                        "amount":      pay["amount"],
                        "invoice_no":  inv["invoice_no"],
                        "inv_sum":     inv.get("sum_uah"),
                        "match_type":  mtype,
                        "match_label": label_map.get(mtype, mtype),
                        "purpose":     pay.get("purpose", ""),
                        "selectable":  True,
                        "row_pay_date": pay_date_str,
                    })

        stats = {
            "total":    len(payments),
            "exact":    sum(1 for r in results if any(m["match_type"] == "exact"  for m in r["matches"])),
            "fuzzy":    sum(1 for r in results if any(m["match_type"] == "fuzzy"  for m in r["matches"])),
            "edrpou":   sum(1 for r in results if any(m["match_type"] == "edrpou" for m in r["matches"])),
            "lyqpay":   sum(1 for r in results if "lyqpay" in r["payment"].get("purpose","").lower() or "liqpay" in r["payment"].get("purpose","").lower()),
            "no_match": sum(1 for r in results if not r["matches"] and "lyqpay" not in r["payment"].get("purpose","").lower() and "liqpay" not in r["payment"].get("purpose","").lower()),
            "unpaid":   len(invoices),
            "lyqpay_saved": lyqpay_saved,
        }

        message = f"📱 lyqpay збережено в додаткові оплати: {lyqpay_saved}" if lyqpay_saved else ""

        return templates.TemplateResponse("payments.html", {
            "request":    request,
            "results":    display,
            "stats":      stats,
            "days":       days,
            "date_from":  date_from.strftime("%d.%m.%Y"),
            "date_to":    date_to.strftime("%d.%m.%Y"),
            "message":    message,
            "error":      "",
            "token_ok":   True,
        })

    except Exception as e:
        return templates.TemplateResponse("payments.html", {
            "request":  request,
            "results":  [],
            "stats":    {},
            "days":     days,
            "date_from": "",
            "date_to":   "",
            "message":  "",
            "error":    f"Помилка API Monobank: {e}",
            "token_ok": bool(mc.get_token()),
        })


def _safe_mark_paid(db, invoice_no: str, pay_date: str) -> bool:
    """Mark invoice as paid only if pay_date >= invoice_date. Returns True if updated."""
    inv = db.execute(
        "SELECT invoice_date FROM invoices WHERE invoice_no=?", (invoice_no,)
    ).fetchone()
    if not inv:
        return False
    inv_date = parse_date(inv["invoice_date"])
    pd       = parse_date(pay_date)
    if inv_date and pd and pd < inv_date:
        return False  # pay_date is before invoice was issued — skip
    db.execute(
        "UPDATE invoices SET pay_status='Оплачено', pay_date=? WHERE invoice_no=?",
        (pay_date, invoice_no)
    )
    return True


@router.post("/mark-paid")
async def mark_invoice_paid(
    invoice_no: str = Form(...),
    pay_date:   str = Form(...),
):
    with get_db() as db:
        _safe_mark_paid(db, invoice_no, pay_date)
    return RedirectResponse("/payments", status_code=303)


@router.post("/mark-paid-bulk")
async def mark_paid_bulk(request: Request):
    form  = await request.form()
    pairs = []
    i = 0
    while True:
        inv_no   = form.get(f"invoice_no_{i}")
        pay_date = form.get(f"pay_date_{i}")
        if inv_no is None:
            break
        pairs.append((pay_date, inv_no))
        i += 1

    if pairs:
        with get_db() as db:
            for (pay_date, inv_no) in pairs:
                _safe_mark_paid(db, inv_no, pay_date)
    return RedirectResponse("/payments", status_code=303)
