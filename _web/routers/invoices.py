"""Invoices router — list, create, update status."""

import os
from datetime import date, timedelta
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from database import get_db
from models import fmt_money, fmt_date, norm, parse_date, is_overdue, pdf_url, make_xlsx

from utils import (calc_due_date_access, count_months,
                   amount_to_words_uah, calc_access_tariff_uah,
                   fmt_money as _app_fmt_money)
from word_handler import (generate_invoice_access, generate_invoice_hourly,
                           build_invoice_pdf_path, convert_to_pdf,
                           generate_reminder, build_reminder_pdf_path)
from email_handler import (send_email, email_configured,
                            body_invoice, body_reminder,
                            EMAIL_FROM_NAME)
from config import CONTRACTS_DIR, TMP_DIR
_DOCS_OK = True

router = APIRouter(prefix="/invoices")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["fmt_money"]  = fmt_money
templates.env.globals["fmt_date"]   = fmt_date
templates.env.globals["norm"]       = norm
templates.env.globals["is_overdue"] = is_overdue
templates.env.globals["pdf_url"]    = pdf_url


def generate_invoice_no(for_date: date) -> str:
    prefix = "RAH-" + for_date.strftime("%d%m%Y")
    with get_db() as db:
        count = db.execute(
            "SELECT COUNT(*) FROM invoices WHERE invoice_no LIKE ?",
            (f"{prefix}%",)
        ).fetchone()[0]
    return f"{prefix}-{str(count + 1).zfill(3)}"


@router.get("", response_class=HTMLResponse)
async def list_invoices(
    request: Request,
    status:  str = Query(default=""),
    client:  str = Query(default=""),
    q:       str = Query(default=""),
    year:    str = Query(default=""),
):
    sql  = "SELECT * FROM invoices WHERE 1=1"
    args = []
    if status:
        sql += " AND pay_status=?"; args.append(status)
    if client:
        sql += " AND client_name LIKE ?"; args.append(f"%{client}%")
    if q:
        sql += " AND (invoice_no LIKE ? OR client_name LIKE ?)"; args += [f"%{q}%", f"%{q}%"]
    if year:
        sql += " AND invoice_date LIKE ?"; args.append(f"%.{year}")
    sql += " ORDER BY invoice_date DESC, invoice_no DESC"

    with get_db() as db:
        rows = db.execute(sql, args).fetchall()
        clients = db.execute(
            "SELECT DISTINCT client_name FROM invoices ORDER BY client_name"
        ).fetchall()
        # Build email lookup: edrpou → email
        client_emails = {
            c["edrpou"]: (c["email"] or "").strip()
            for c in db.execute("SELECT edrpou, email FROM clients").fetchall()
        }
        # contract_no → edrpou lookup
        contract_edrpou = {
            c["contract_no"]: c["edrpou"]
            for c in db.execute("SELECT contract_no, edrpou FROM contracts").fetchall()
        }

    today = date.today()
    invoices = []
    sum_paid    = 0.0
    sum_unpaid  = 0.0
    sum_overdue = 0.0
    sum_cancelled = 0.0

    for r in rows:
        due_str = r["due_date"] or ""
        st      = norm(r["pay_status"])
        overdue = is_overdue(st, due_str)
        amt     = float(r["sum_uah"] or 0)

        if st == "Оплачено":
            sum_paid += amt
        elif st == "Скасовано":
            sum_cancelled += amt
        elif overdue:
            sum_overdue += amt
        else:
            sum_unpaid += amt

        due_d = parse_date(r["due_date"])
        overdue_days = (today - due_d).days if (due_d and today > due_d and st not in ("Оплачено", "Скасовано")) else 0

        # Resolve client email via contract → edrpou → clients
        edrpou = contract_edrpou.get(r["contract_no"] or "", "")
        client_email = client_emails.get(edrpou, "")

        invoices.append({
            "row":              r,
            "status":           st,
            "overdue":          overdue,
            "overdue_days":     overdue_days,
            "reminder_date":    r["reminder_date"] or "",
            "reminder_pdf":     pdf_url(r["reminder_pdf_path"]) if r["reminder_pdf_path"] else "",
            "email_sent_date":  r["email_sent_date"] or "",
            "client_email":     client_email,
        })

    return templates.TemplateResponse("invoices/list.html", {
        "request":       request,
        "invoices":      invoices,
        "clients":       [c["client_name"] for c in clients],
        "today":         today,
        "status":        status,
        "client":        client,
        "q":             q,
        "year":          year,
        "sum_paid":      sum_paid,
        "sum_unpaid":    sum_unpaid,
        "sum_overdue":   sum_overdue,
        "sum_cancelled": sum_cancelled,
        "sum_total":     sum_paid + sum_unpaid + sum_overdue,
    })


@router.get("/export")
async def export_invoices(
    status: str = Query(default=""),
    client: str = Query(default=""),
    q:      str = Query(default=""),
    year:   str = Query(default=""),
):
    sql  = "SELECT * FROM invoices WHERE 1=1"
    args = []
    if status:
        sql += " AND pay_status=?"; args.append(status)
    if client:
        sql += " AND client_name LIKE ?"; args.append(f"%{client}%")
    if q:
        sql += " AND (invoice_no LIKE ? OR client_name LIKE ?)"; args += [f"%{q}%", f"%{q}%"]
    if year:
        sql += " AND invoice_date LIKE ?"; args.append(f"%.{year}")
    sql += " ORDER BY invoice_date DESC, invoice_no DESC"

    with get_db() as db:
        rows = db.execute(sql, args).fetchall()

    headers = ["№ рахунку", "Клієнт", "Договір", "Дата", "Період від", "Період до",
               "Сума (грн)", "Знижка %", "Статус", "Строк оплати", "Дата оплати"]
    data = []
    for r in rows:
        data.append([
            r["invoice_no"],
            r["client_name"],
            r["contract_no"],
            r["invoice_date"],
            r["period_from"],
            r["period_to"],
            float(r["sum_uah"] or 0),
            float(r["discount_pct"] or 0),
            norm(r["pay_status"]),
            r["due_date"],
            r["pay_date"],
        ])

    buf = make_xlsx(headers, data, "Рахунки")
    filename = f"Рахунки_{date.today().strftime('%d%m%Y')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}
    )


@router.get("/new", response_class=HTMLResponse)
async def new_invoice_form(request: Request, contract_no: str = ""):
    with get_db() as db:
        contracts = db.execute(
            "SELECT * FROM contracts WHERE status='Активний' ORDER BY client_name"
        ).fetchall()
        contract = None
        if contract_no:
            contract = db.execute(
                "SELECT * FROM contracts WHERE contract_no=?", (contract_no,)
            ).fetchone()

    today = date.today()
    return templates.TemplateResponse("invoices/form.html", {
        "request":     request,
        "contracts":   contracts,
        "contract":    contract,
        "today":       today,
        "today_str":   fmt_date(today),
        "title":       "Новий рахунок",
    })


@router.post("/new")
async def create_invoice(
    request:       Request,
    contract_no:   str   = Form(...),
    invoice_date:  str   = Form(...),
    period_from:   str   = Form(""),
    period_to:     str   = Form(""),
    fx_rate:       float = Form(0),
    months:        int   = Form(0),
    hours:         float = Form(0),
    discount_pct:  float = Form(0),
):
    with get_db() as db:
        contract = db.execute(
            "SELECT * FROM contracts WHERE contract_no=?", (contract_no,)
        ).fetchone()
        client_row = db.execute(
            "SELECT address, director FROM clients WHERE edrpou=?",
            (contract["edrpou"],)
        ).fetchone() if contract else None

    if not contract:
        return HTMLResponse("Договір не знайдено", status_code=400)

    inv_date = parse_date(invoice_date) or date.today()
    p_from   = parse_date(period_from)
    p_to     = parse_date(period_to)
    due      = calc_due_date_access(inv_date)

    currency  = contract["currency"] or "UAH"
    tariff_fx = float(contract["tariff_fx"] or 0)
    users     = int(contract["users"] or 1)
    ctype     = norm(contract["contract_type"])

    if ctype == "Доступ":
        if not months and p_from and p_to:
            months = count_months(p_from, p_to)
        if not months:
            months = 1
        tariff_uah = tariff_fx * fx_rate if (currency != "UAH" and fx_rate) else tariff_fx
        sum_fx     = tariff_fx * users * months
        sum_uah    = round(tariff_uah * users * months * (1 - discount_pct / 100), 2)
    else:
        hour_rate = float(contract["hour_rate"] or 0)
        if not hours:
            hours = 1.0
        tariff_uah = hour_rate
        sum_fx     = hour_rate * hours
        sum_uah    = round(sum_fx, 2)
        months     = 0

    inv_no = generate_invoice_no(inv_date)

    with get_db() as db:
        db.execute(
            "INSERT INTO invoices "
            "(invoice_no,contract_no,client_name,invoice_date,fx_rate,currency,"
            "sum_fx,sum_uah,period_from,period_to,due_date,pay_status,"
            "invoice_type,months,discount_pct) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,'Не оплачено',?,?,?)",
            (inv_no, contract_no, contract["client_name"],
             fmt_date(inv_date), fx_rate or None, currency,
             round(sum_fx, 2), sum_uah,
             fmt_date(p_from), fmt_date(p_to), fmt_date(due),
             ctype, months or None, discount_pct)
        )

    return RedirectResponse(f"/invoices/{inv_no}/preview", status_code=303)


@router.get("/{invoice_no}/preview", response_class=HTMLResponse)
async def preview_invoice(request: Request, invoice_no: str):
    """Відкриває in-app HTML-редактор для рахунку."""
    with get_db() as db:
        inv = db.execute(
            "SELECT * FROM invoices WHERE invoice_no=?", (invoice_no,)
        ).fetchone()
        if not inv:
            return HTMLResponse("Рахунок не знайдено", status_code=404)
        contract = db.execute(
            "SELECT * FROM contracts WHERE contract_no=?", (inv["contract_no"],)
        ).fetchone() if inv["contract_no"] else None
        client_row = db.execute(
            "SELECT address FROM clients WHERE edrpou=?", (contract["edrpou"],)
        ).fetchone() if contract else None

    ctype        = norm(contract["contract_type"] or "") if contract else ""
    sum_uah      = float(inv["sum_uah"] or 0)
    discount_pct = float(inv["discount_pct"] or 0)
    gross        = round(sum_uah / (1 - discount_pct / 100), 2) if discount_pct else sum_uah
    discount_amt = round(gross - sum_uah, 2)

    tariff_fx  = float(contract["tariff_fx"] or 0) if contract else 0
    fx_rate    = float(inv["fx_rate"] or 1) or 1
    currency   = inv["currency"] or "UAH"
    tariff_uah = tariff_fx * fx_rate if (currency != "UAH" and fx_rate) else tariff_fx
    hour_rate  = float(contract["hour_rate"] or 0) if contract else 0
    sum_fx     = float(inv["sum_fx"] or 0)
    hours      = round(sum_fx / hour_rate, 1) if hour_rate else 0.0

    d = {
        "invoice_no":        invoice_no,
        "invoice_date_str":  inv["invoice_date"] or "",
        "contract_no":       inv["contract_no"] or "",
        "client_name":       inv["client_name"] or "",
        "client_address":    (client_row["address"] or "") if client_row else "",
        "users":             int(contract["users"] or 1) if contract else 1,
        "months":            int(inv["months"] or 1),
        "hours":             hours,
        "hour_rate_str":     _app_fmt_money(hour_rate),
        "subject":           (contract["subject"] or "") if contract else "",
        "contract_date_str": (contract["contract_date"] or "") if contract else "",
        "period_from_str":   inv["period_from"] or "",
        "period_to_str":     inv["period_to"] or "",
        "due_date_str":      inv["due_date"] or "",
        "tariff_str":        _app_fmt_money(tariff_uah),
        "sum_gross_str":     _app_fmt_money(gross),
        "discount_pct":      discount_pct,
        "discount_amt_str":  _app_fmt_money(discount_amt),
        "sum_str":           _app_fmt_money(sum_uah),
        "sum_words":         inv["sum_words"] or amount_to_words_uah(sum_uah),
        "sum_uah":           sum_uah,
    }

    # Визначаємо потрібний partial-шаблон
    inv_type = norm(inv["invoice_type"] or "")
    if ctype == "Доступ" or inv_type in ("Доступ", "Access"):
        doc_template = "docs/invoice_access.html"
    else:
        doc_template = "docs/invoice_hourly.html"

    pdf_url_val = pdf_url(inv["pdf_path"]) if inv["pdf_path"] else ""

    return templates.TemplateResponse("doc_editor.html", {
        "request":           request,
        "d":                 d,
        "doc_template":      doc_template,
        "back_url":          "/invoices",
        "title":             f"Рахунок {invoice_no}",
        "generate_pdf_url":  f"/invoices/{invoice_no}/generate-pdf",
        "pdf_url_val":       pdf_url_val,
    })


@router.post("/{invoice_no}/generate-pdf")
async def generate_pdf_invoice(invoice_no: str):
    """Генерує Word → PDF через LibreOffice, зберігає в Договори/, оновлює pdf_path в БД."""
    with get_db() as db:
        inv = db.execute(
            "SELECT * FROM invoices WHERE invoice_no=?", (invoice_no,)
        ).fetchone()
        if not inv:
            return HTMLResponse("Рахунок не знайдено", status_code=404)
        contract = db.execute(
            "SELECT * FROM contracts WHERE contract_no=?", (inv["contract_no"],)
        ).fetchone() if inv["contract_no"] else None
        client_row = db.execute(
            "SELECT address FROM clients WHERE edrpou=?", (contract["edrpou"],)
        ).fetchone() if contract else None

    ctype        = norm(contract["contract_type"] or "") if contract else ""
    sum_uah      = float(inv["sum_uah"] or 0)
    discount_pct = float(inv["discount_pct"] or 0)
    gross        = round(sum_uah / (1 - discount_pct / 100), 2) if discount_pct else sum_uah
    discount_amt = round(gross - sum_uah, 2)
    tariff_fx    = float(contract["tariff_fx"] or 0) if contract else 0
    fx_rate      = float(inv["fx_rate"] or 1) or 1
    currency     = inv["currency"] or "UAH"
    tariff_uah   = tariff_fx * fx_rate if (currency != "UAH" and fx_rate) else tariff_fx
    hour_rate    = float(contract["hour_rate"] or 0) if contract else 0
    sum_fx       = float(inv["sum_fx"] or 0)
    hours        = round(sum_fx / hour_rate, 1) if hour_rate else 0.0

    doc_data = {
        "invoice_no":        invoice_no,
        "invoice_date_str":  inv["invoice_date"] or "",
        "contract_no":       inv["contract_no"] or "",
        "client_name":       inv["client_name"] or "",
        "client_address":    (client_row["address"] or "") if client_row else "",
        "users":             int(contract["users"] or 1) if contract else 1,
        "months":            int(inv["months"] or 1),
        "hours":             hours,
        "hour_rate":         hour_rate,
        "subject":           (contract["subject"] or "") if contract else "",
        "contract_date_str": (contract["contract_date"] or "") if contract else "",
        "period_from_str":   inv["period_from"] or "",
        "period_to_str":     inv["period_to"] or "",
        "due_date_str":      inv["due_date"] or "",
        "tariff_str":        _app_fmt_money(tariff_uah),
        "sum_gross_str":     _app_fmt_money(gross),
        "discount_pct":      discount_pct,
        "discount_amt_str":  _app_fmt_money(discount_amt),
        "sum_str":           _app_fmt_money(sum_uah),
        "sum_words":         inv["sum_words"] or amount_to_words_uah(sum_uah),
        "sum_uah":           sum_uah,
    }

    try:
        if ctype == "Доступ":
            docx_path = generate_invoice_access(doc_data)
        else:
            docx_path = generate_invoice_hourly(doc_data)

        pdf_path_out = build_invoice_pdf_path(
            inv["client_name"] or "", inv["contract_no"] or "", invoice_no
        )
        convert_to_pdf(docx_path, pdf_path_out)

        with get_db() as db:
            db.execute(
                "UPDATE invoices SET pdf_path=? WHERE invoice_no=?",
                (pdf_path_out, invoice_no)
            )
    except Exception:
        pass  # Помилка конвертації — повертаємо в редактор без pdf_url

    return RedirectResponse(f"/invoices/{invoice_no}/preview?saved=1", status_code=303)


@router.post("/{invoice_no}/finalize")
async def finalize_invoice(request: Request, invoice_no: str):
    """Конвертує відредагований DOCX у PDF та зберігає в папку клієнта."""
    with get_db() as db:
        inv = db.execute(
            "SELECT * FROM invoices WHERE invoice_no=?", (invoice_no,)
        ).fetchone()

    doc_error   = ""
    pdf_url_val = ""
    docx_name   = ""

    if not _DOCS_OK:
        doc_error = "Модулі генерації документів недоступні"
    elif not inv:
        doc_error = "Рахунок не знайдено"
    else:
        docx_path = os.path.join(TMP_DIR, f"Рахунок_{invoice_no}.docx")
        docx_name = os.path.basename(docx_path)
        if not os.path.exists(docx_path):
            doc_error = f"Word-файл не знайдено ({docx_name}).\nМожливо, сервер перезапускався — створіть рахунок ще раз."
        else:
            try:
                pdf_path = build_invoice_pdf_path(
                    inv["client_name"], inv["contract_no"] or "", invoice_no
                )
                convert_to_pdf(docx_path, pdf_path)
                with get_db() as db:
                    db.execute(
                        "UPDATE invoices SET pdf_path=? WHERE invoice_no=?",
                        (pdf_path, invoice_no)
                    )
                rel         = pdf_path[len(CONTRACTS_DIR):].lstrip("/\\")
                pdf_url_val = f"/docs/{rel}"
            except Exception as e:
                doc_error = str(e)

    return templates.TemplateResponse("invoices/done.html", {
        "request":   request,
        "inv_no":    invoice_no,
        "client":    inv["client_name"] if inv else "",
        "sum_uah":   float(inv["sum_uah"] or 0) if inv else 0,
        "docx_name": docx_name,
        "pdf_url":   pdf_url_val,
        "doc_error": doc_error,
    })


@router.get("/{invoice_no}/docx")
async def download_invoice_docx(invoice_no: str):
    """Serve the Word file from TMP_DIR."""
    fname = f"Рахунок_{invoice_no}.docx"
    path  = os.path.join(TMP_DIR, fname)
    if not os.path.exists(path):
        return HTMLResponse("Файл не знайдено (можливо, сервер перезапускався)", status_code=404)
    return FileResponse(path, filename=fname,
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@router.get("/{invoice_no}/edit", response_class=HTMLResponse)
async def edit_invoice_form(request: Request, invoice_no: str):
    with get_db() as db:
        inv = db.execute(
            "SELECT * FROM invoices WHERE invoice_no=?", (invoice_no,)
        ).fetchone()
    if not inv:
        return HTMLResponse("Рахунок не знайдено", status_code=404)
    return templates.TemplateResponse("invoices/edit.html", {
        "request": request,
        "inv":     inv,
        "title":   f"Редагувати {invoice_no}",
    })


@router.post("/{invoice_no}/edit")
async def edit_invoice(
    invoice_no:   str,
    invoice_date: str   = Form(""),
    period_from:  str   = Form(""),
    period_to:    str   = Form(""),
    fx_rate:      float = Form(0),
    sum_uah:      float = Form(...),
    discount_pct: float = Form(0),
    pay_status:   str   = Form("Не оплачено"),
    pay_date:     str   = Form(""),
):
    with get_db() as db:
        db.execute(
            """UPDATE invoices SET
               invoice_date=?, period_from=?, period_to=?,
               fx_rate=?, sum_uah=?, discount_pct=?,
               pay_status=?, pay_date=?
               WHERE invoice_no=?""",
            (
                invoice_date or None,
                period_from  or None,
                period_to    or None,
                fx_rate      or None,
                round(sum_uah, 2),
                discount_pct,
                pay_status,
                pay_date or None,
                invoice_no,
            )
        )
    return RedirectResponse("/invoices", status_code=303)


@router.post("/{invoice_no}/delete")
async def delete_invoice(invoice_no: str):
    with get_db() as db:
        # Also delete linked act if exists
        db.execute("DELETE FROM acts    WHERE invoice_no=?", (invoice_no,))
        db.execute("DELETE FROM invoices WHERE invoice_no=?", (invoice_no,))
    return RedirectResponse("/invoices", status_code=303)


@router.post("/{invoice_no}/mark-paid")
async def mark_paid(invoice_no: str, pay_date: str = Form("")):
    pd = parse_date(pay_date) or date.today()
    with get_db() as db:
        db.execute(
            "UPDATE invoices SET pay_status='Оплачено', pay_date=? WHERE invoice_no=?",
            (fmt_date(pd), invoice_no)
        )
    return RedirectResponse("/invoices", status_code=303)


@router.post("/{invoice_no}/cancel")
async def cancel_invoice(invoice_no: str):
    with get_db() as db:
        db.execute(
            "UPDATE invoices SET pay_status='Скасовано' WHERE invoice_no=?",
            (invoice_no,)
        )
    return RedirectResponse("/invoices", status_code=303)


@router.post("/{invoice_no}/send-reminder")
async def send_reminder(invoice_no: str):
    """Генерує лист-нагадування, конвертує в PDF, фіксує дату відправлення."""
    with get_db() as db:
        inv = db.execute(
            "SELECT * FROM invoices WHERE invoice_no=?", (invoice_no,)
        ).fetchone()
        if not inv:
            return HTMLResponse("Рахунок не знайдено", status_code=404)
        contract = db.execute(
            "SELECT * FROM contracts WHERE contract_no=?", (inv["contract_no"],)
        ).fetchone() if inv["contract_no"] else None
        client_row = db.execute(
            "SELECT address FROM clients WHERE edrpou=?", (contract["edrpou"],)
        ).fetchone() if contract else None

    today     = date.today()
    due_d     = parse_date(inv["due_date"])
    overdue_days = (today - due_d).days if (due_d and today > due_d) else 0
    sum_uah   = float(inv["sum_uah"] or 0)

    reminder_data = {
        "invoice_no":       invoice_no,
        "invoice_date_str": inv["invoice_date"] or "",
        "contract_no":      inv["contract_no"] or "",
        "client_name":      inv["client_name"] or "",
        "client_address":   (client_row["address"] or "") if client_row else "",
        "sum_str":          _app_fmt_money(sum_uah),
        "due_date_str":     inv["due_date"] or "",
        "overdue_days":     overdue_days,
        "our_name":         "DocketPro",
    }

    try:
        docx_path    = generate_reminder(reminder_data)
        pdf_path_out = build_reminder_pdf_path(
            inv["client_name"] or "", inv["contract_no"] or "", invoice_no
        )
        convert_to_pdf(docx_path, pdf_path_out)

        with get_db() as db:
            db.execute(
                "UPDATE invoices SET reminder_date=?, reminder_pdf_path=? WHERE invoice_no=?",
                (fmt_date(today), pdf_path_out, invoice_no)
            )

        # Якщо є email клієнта та SMTP налаштовано — також відправити листом
        if email_configured() and client_row:
            client_email = ""
            if contract:
                with get_db() as db:
                    ce = db.execute(
                        "SELECT email FROM clients WHERE edrpou=?", (contract["edrpou"],)
                    ).fetchone()
                    client_email = (ce["email"] or "").strip() if ce else ""
            if client_email:
                overdue_days_int = int(overdue_days)
                if overdue_days_int > 0:
                    due_line = f"Термін оплати минув {overdue_days_int} днів тому (строк: {inv['due_date'] or ''})."
                else:
                    due_line = f"Термін оплати: {inv['due_date'] or ''}."
                subject = f"Нагадування: рахунок {invoice_no}"
                await send_email(
                    to_email=client_email,
                    subject=subject,
                    body_html=body_reminder(
                        invoice_no=invoice_no,
                        client_name=inv["client_name"] or "",
                        sum_str=_app_fmt_money(sum_uah),
                        due_line=due_line,
                        our_name=EMAIL_FROM_NAME,
                    ),
                    attachment_path=pdf_path_out,
                    attachment_name=f"Нагадування_{invoice_no}.pdf",
                )
    except Exception:
        pass  # PDF або email помилка — не блокуємо роботу

    return RedirectResponse("/invoices", status_code=303)


# ── Відправити рахунок на email ───────────────────────────────────

@router.post("/{invoice_no}/send-email")
async def send_invoice_email(invoice_no: str):
    """Надсилає PDF рахунку на email клієнта."""
    if not email_configured():
        return RedirectResponse("/invoices?toast=nosmtp", status_code=303)

    with get_db() as db:
        inv = db.execute(
            "SELECT * FROM invoices WHERE invoice_no=?", (invoice_no,)
        ).fetchone()
        if not inv:
            return RedirectResponse("/invoices?toast=notfound", status_code=303)

        contract = db.execute(
            "SELECT * FROM contracts WHERE contract_no=?", (inv["contract_no"],)
        ).fetchone() if inv["contract_no"] else None

        client_email = ""
        if contract:
            ce = db.execute(
                "SELECT email FROM clients WHERE edrpou=?", (contract["edrpou"],)
            ).fetchone()
            client_email = (ce["email"] or "").strip() if ce else ""

    if not client_email:
        return RedirectResponse("/invoices?toast=noemail", status_code=303)

    pdf_path = inv["pdf_path"] or ""
    if not pdf_path or not os.path.exists(pdf_path):
        return RedirectResponse("/invoices?toast=nopdf", status_code=303)

    sum_uah = float(inv["sum_uah"] or 0)
    try:
        await send_email(
            to_email=client_email,
            subject=f"Рахунок {invoice_no}",
            body_html=body_invoice(
                invoice_no=invoice_no,
                client_name=inv["client_name"] or "",
                sum_str=_app_fmt_money(sum_uah),
                due_date=inv["due_date"] or "",
                our_name=EMAIL_FROM_NAME,
            ),
            attachment_path=pdf_path,
            attachment_name=f"Рахунок_{invoice_no}.pdf",
        )
        with get_db() as db:
            db.execute(
                "UPDATE invoices SET email_sent_date=? WHERE invoice_no=?",
                (fmt_date(date.today()), invoice_no)
            )
        return RedirectResponse(f"/invoices?toast=email_ok&to={client_email}", status_code=303)
    except Exception as exc:
        return RedirectResponse(f"/invoices?toast=email_error", status_code=303)
