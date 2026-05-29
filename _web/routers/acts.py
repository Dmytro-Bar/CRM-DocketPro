"""Acts router — list, create, update status."""

import os
from datetime import date
from typing import Optional
from urllib.parse import quote
from fastapi import APIRouter, Request, Form, Query, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from database import get_db
from models import fmt_money, fmt_date, norm, parse_date, pdf_url, make_xlsx, get_active_emails

from utils import amount_to_words_uah, fmt_money as _app_fmt_money
from word_handler import (generate_act_with_template, resolve_act_template,
                           build_act_pdf_path, convert_to_pdf)
from config import CONTRACTS_DIR, TMP_DIR
from email_handler import send_email, email_configured, body_act, EMAIL_FROM_NAME
from utils import fmt_money as _app_fmt_money
from routers.sig_utils import apply_signature_to_pdf
_DOCS_OK = True

router = APIRouter(prefix="/acts")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["fmt_money"] = fmt_money
templates.env.globals["fmt_date"]  = fmt_date
templates.env.globals["norm"]      = norm
templates.env.globals["pdf_url"]   = pdf_url



def _generate_act_pdf(act_no: str) -> Optional[str]:
    """Generate Word → PDF for an existing act record. Returns pdf_path or None."""
    with get_db() as db:
        act = db.execute("SELECT * FROM acts WHERE act_no=?", (act_no,)).fetchone()
        if not act:
            return None
        inv = db.execute(
            "SELECT * FROM invoices WHERE invoice_no=?", (act["invoice_no"],)
        ).fetchone() if act["invoice_no"] else None
        contract = db.execute(
            "SELECT * FROM contracts WHERE contract_no=?", (act["contract_no"],)
        ).fetchone() if act["contract_no"] else None
        client_row = db.execute(
            "SELECT edrpou, address, director FROM clients WHERE edrpou=?",
            (contract["edrpou"],)
        ).fetchone() if contract else None

    ctype     = norm(contract["contract_type"] or "") if contract else ""
    sum_uah   = float(act["sum_uah"] or 0)
    hour_rate = float(contract["hour_rate"] or 0) if contract else 0
    sum_fx    = float(inv["sum_fx"] or 0) if inv else 0
    hours     = round(sum_fx / hour_rate, 1) if hour_rate else 0.0

    doc_data = {
        "act_no":            act_no,
        "act_date_str":      act["act_date"] or "",
        "contract_no":       act["contract_no"] or "",
        "contract_date_str": (contract["contract_date"] or "") if contract else "",
        "client_name":       act["client_name"] or "",
        "edrpou":            (client_row["edrpou"]  or "") if client_row else "",
        "client_director":   (client_row["director"] or "") if client_row else "",
        "period_from_str":   act["period_from"] or "",
        "period_to_str":     act["period_to"] or "",
        "users":             int(inv["users"] or 1) if inv else 1,
        "months":            int(inv["months"] or 1) if inv else 1,
        "hours":             hours,
        "hour_rate_str":     _app_fmt_money(hour_rate),
        "tariff_str":        _app_fmt_money(float(contract["tariff_fx"] or 0) if contract else 0),
        "subject":           (contract["subject"] or "") if contract else "",
        "sum_str":           _app_fmt_money(sum_uah),
        "sum_words":         amount_to_words_uah(sum_uah),
        "sum_uah":           sum_uah,
    }

    try:
        act_template_file = (contract["act_template"] or "") if contract else ""
        tpl_path  = resolve_act_template(ctype, act_template_file)
        docx_path = generate_act_with_template(doc_data, tpl_path)

        pdf_path_out = build_act_pdf_path(
            act["client_name"] or "", act["contract_no"] or "", act_no
        )
        convert_to_pdf(docx_path, pdf_path_out)

        with get_db() as db:
            db.execute(
                "UPDATE acts SET pdf_path=? WHERE act_no=?",
                (pdf_path_out, act_no)
            )
        return pdf_path_out
    except Exception:
        return None


def generate_act_no() -> str:
    today = date.today()
    prefix = "ACT-" + today.strftime("%d%m%Y")
    with get_db() as db:
        rows = db.execute(
            "SELECT act_no FROM acts WHERE act_no LIKE ?",
            (f"{prefix}-%",)
        ).fetchall()
    # Use MAX of existing sequence numbers to avoid conflicts after deletions
    max_n = 0
    for row in rows:
        try:
            max_n = max(max_n, int(row["act_no"].rsplit("-", 1)[-1]))
        except (ValueError, IndexError):
            pass
    return f"{prefix}-{str(max_n + 1).zfill(3)}"


@router.get("", response_class=HTMLResponse)
async def list_acts(
    request: Request,
    status:  str = Query(default=""),
    client:  str = Query(default=""),
    q:       str = Query(default=""),
):
    sql  = "SELECT * FROM acts WHERE 1=1"
    args = []
    if status:
        sql += " AND status=?"; args.append(status)
    if client:
        sql += " AND client_name LIKE ?"; args.append(f"%{client}%")
    if q:
        sql += " AND (act_no LIKE ? OR client_name LIKE ?)"; args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY act_date DESC, act_no DESC"

    with get_db() as db:
        rows = db.execute(sql, args).fetchall()
        clients = db.execute(
            "SELECT DISTINCT client_name FROM acts ORDER BY client_name"
        ).fetchall()
        client_emails = {
            c["edrpou"]: get_active_emails(c)
            for c in db.execute(
                "SELECT edrpou, email, email_active, email2, email2_active, email3, email3_active FROM clients"
            ).fetchall()
        }
        contract_edrpou = {
            c["contract_no"]: c["edrpou"]
            for c in db.execute("SELECT contract_no, edrpou FROM contracts").fetchall()
        }

    acts = []
    today = date.today()
    for r in rows:
        st = norm(r["status"]) or "Чернетка"
        ad = parse_date(r["act_date"])
        days = (today - ad).days if ad else 0
        edrpou = contract_edrpou.get(r["contract_no"] or "", "")
        client_email = ", ".join(client_emails.get(edrpou, []))
        acts.append({
            "row":             r,
            "status":          st,
            "days":            days,
            "email_sent_date": r["email_sent_date"] or "",
            "client_email":    client_email,
            "scan_pdf":        pdf_url(r["scan_path"]) if r["scan_path"] else "",
        })

    return templates.TemplateResponse("acts/list.html", {
        "request": request,
        "acts":    acts,
        "clients": [c["client_name"] for c in clients],
        "status":  status,
        "client":  client,
        "q":       q,
    })


@router.get("/export")
async def export_acts(
    status: str = Query(default=""),
    client: str = Query(default=""),
    q:      str = Query(default=""),
):
    sql  = "SELECT * FROM acts WHERE 1=1"
    args = []
    if status:
        sql += " AND status=?"; args.append(status)
    if client:
        sql += " AND client_name LIKE ?"; args.append(f"%{client}%")
    if q:
        sql += " AND (act_no LIKE ? OR client_name LIKE ?)"; args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY act_date DESC, act_no DESC"

    with get_db() as db:
        rows = db.execute(sql, args).fetchall()

    headers = ["№ акту", "Клієнт", "Договір", "Рахунок", "Дата акту",
               "Період від", "Період до", "Сума (грн)", "Статус"]
    data = []
    for r in rows:
        data.append([
            r["act_no"],
            r["client_name"],
            r["contract_no"],
            r["invoice_no"],
            r["act_date"],
            r["period_from"],
            r["period_to"],
            float(r["sum_uah"] or 0),
            norm(r["status"]),
        ])

    buf = make_xlsx(headers, data, "Акти")
    filename = f"Акти_{date.today().strftime('%d%m%Y')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}
    )


@router.get("/new", response_class=HTMLResponse)
async def new_act_form(request: Request, invoice_no: str = ""):
    """Show invoices that don't have an act yet (Access type)."""
    with get_db() as db:
        # Invoices without acts
        existing_act_inv = {
            r["invoice_no"]
            for r in db.execute(
                "SELECT DISTINCT invoice_no FROM acts WHERE invoice_no IS NOT NULL"
            ).fetchall()
        }
        invoices = db.execute(
            "SELECT i.*, c.contract_type FROM invoices i "
            "LEFT JOIN contracts c ON i.contract_no = c.contract_no "
            "WHERE i.pay_status != 'Скасовано' "
            "ORDER BY i.invoice_date DESC"
        ).fetchall()

    available = [
        inv for inv in invoices
        if inv["invoice_no"] not in existing_act_inv
    ]

    selected = None
    if invoice_no:
        for inv in available:
            if inv["invoice_no"] == invoice_no:
                selected = inv
                break

    today = date.today()
    return templates.TemplateResponse("acts/form.html", {
        "request":   request,
        "invoices":  available,
        "selected":  selected,
        "today_str": fmt_date(today),
        "title":     "Новий акт",
    })


@router.post("/new")
async def create_act(
    request:    Request,
    invoice_no: str  = Form(...),
    act_date:   str  = Form(...),
    status:     str  = Form("Чернетка"),
):
    with get_db() as db:
        inv = db.execute(
            "SELECT * FROM invoices WHERE invoice_no=?", (invoice_no,)
        ).fetchone()
        contract = db.execute(
            "SELECT * FROM contracts WHERE contract_no=?",
            (inv["contract_no"],)
        ).fetchone() if inv else None
        client_row = db.execute(
            "SELECT edrpou, address, director FROM clients WHERE edrpou=?",
            (contract["edrpou"],)
        ).fetchone() if contract else None

    if not inv:
        return HTMLResponse("Рахунок не знайдено", status_code=400)

    act_no = generate_act_no()
    ad     = parse_date(act_date) or date.today()

    with get_db() as db:
        db.execute(
            "INSERT INTO acts "
            "(act_no,invoice_no,contract_no,client_name,act_date,"
            "period_from,period_to,sum_uah,status) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (act_no, invoice_no, inv["contract_no"], inv["client_name"],
             fmt_date(ad), inv["period_from"], inv["period_to"],
             inv["sum_uah"], status)
        )

    # Generate PDF immediately — no preview step needed
    _generate_act_pdf(act_no)

    return RedirectResponse("/acts", status_code=303)


@router.get("/{act_no}/preview", response_class=HTMLResponse)
async def preview_act(request: Request, act_no: str):
    """Відкриває in-app HTML-редактор для акту."""
    with get_db() as db:
        act = db.execute(
            "SELECT * FROM acts WHERE act_no=?", (act_no,)
        ).fetchone()
        if not act:
            return HTMLResponse("Акт не знайдено", status_code=404)
        inv = db.execute(
            "SELECT * FROM invoices WHERE invoice_no=?", (act["invoice_no"],)
        ).fetchone() if act["invoice_no"] else None
        contract = db.execute(
            "SELECT * FROM contracts WHERE contract_no=?", (act["contract_no"],)
        ).fetchone() if act["contract_no"] else None
        client_row = db.execute(
            "SELECT edrpou, address, director FROM clients WHERE edrpou=?",
            (contract["edrpou"],)
        ).fetchone() if contract else None

    ctype     = norm(contract["contract_type"] or "") if contract else ""
    sum_uah   = float(act["sum_uah"] or 0)
    tariff_fx = float(contract["tariff_fx"] or 0) if contract else 0
    hour_rate = float(contract["hour_rate"] or 0) if contract else 0

    # Відновлюємо кількість годин для погодинного типу
    sum_fx = float(inv["sum_fx"] or 0) if inv else 0
    hours  = round(sum_fx / hour_rate, 1) if hour_rate else 0.0

    d = {
        "act_no":            act_no,
        "act_date_str":      act["act_date"] or "",
        "contract_no":       act["contract_no"] or "",
        "contract_date_str": (contract["contract_date"] or "") if contract else "",
        "client_name":       act["client_name"] or "",
        "edrpou":            (client_row["edrpou"]  or "") if client_row else "",
        "client_director":   (client_row["director"] or "") if client_row else "",
        "period_from_str":   act["period_from"] or "",
        "period_to_str":     act["period_to"] or "",
        "users":             int(inv["users"] or 1) if inv else 1,
        "months":            int(inv["months"] or 1) if inv else 1,
        "hours":             hours,
        "hour_rate_str":     _app_fmt_money(hour_rate),
        "tariff_str":        _app_fmt_money(tariff_fx),
        "subject":           (contract["subject"] or "") if contract else "",
        "sum_str":           _app_fmt_money(sum_uah),
        "sum_words":         amount_to_words_uah(sum_uah),
        "sum_uah":           sum_uah,
    }

    doc_template = (
        "docs/act_access.html" if ctype == "Доступ"
        else "docs/act_hourly.html"
    )

    # Custom .docx template assigned to this contract?
    custom_template_name = ""
    if contract and contract["act_template"]:
        custom_template_name = contract["act_template"]

    pdf_url_val = pdf_url(act["pdf_path"]) if act["pdf_path"] else ""

    return templates.TemplateResponse("doc_editor.html", {
        "request":              request,
        "d":                    d,
        "doc_template":         doc_template,
        "back_url":             "/acts",
        "title":                f"Акт {act_no}",
        "generate_pdf_url":     f"/acts/{act_no}/generate-pdf",
        "pdf_url_val":          pdf_url_val,
        "custom_template_name": custom_template_name,
    })


@router.post("/{act_no}/generate-pdf")
async def generate_pdf_act(act_no: str):
    """(Re)generate Word → PDF for an existing act. Used from preview page."""
    _generate_act_pdf(act_no)
    return RedirectResponse(f"/acts/{act_no}/preview?saved=1", status_code=303)


@router.post("/{act_no}/apply-signature")
async def apply_signature_act(act_no: str, request: Request):
    """Overlay the configured signature image onto the act PDF."""
    with get_db() as db:
        act = db.execute("SELECT pdf_path FROM acts WHERE act_no=?", (act_no,)).fetchone()

    if not act or not act["pdf_path"]:
        msg = "PDF акту не знайдено — спочатку згенеруйте PDF"
        if request.headers.get("HX-Request"):
            return HTMLResponse(f'<span style="color:var(--danger-fg)">{msg}</span>')
        return HTMLResponse(msg, status_code=400)

    ok, err = apply_signature_to_pdf(act["pdf_path"])

    if request.headers.get("HX-Request"):
        if ok:
            return HTMLResponse('<span style="color:var(--success-fg)">✓ Підпис накладено</span>')
        return HTMLResponse(f'<span style="color:var(--danger-fg)">{err}</span>')

    return RedirectResponse("/acts", status_code=303)


@router.post("/{act_no}/finalize")
async def finalize_act(request: Request, act_no: str):
    """Конвертує відредагований DOCX у PDF та зберігає в папку клієнта."""
    with get_db() as db:
        act = db.execute("SELECT * FROM acts WHERE act_no=?", (act_no,)).fetchone()

    doc_error   = ""
    pdf_url_val = ""
    docx_name   = ""

    if not _DOCS_OK:
        doc_error = "Модулі генерації документів недоступні"
    elif not act:
        doc_error = "Акт не знайдено"
    else:
        docx_path = os.path.join(TMP_DIR, f"Акт_{act_no}.docx")
        docx_name = os.path.basename(docx_path)
        if not os.path.exists(docx_path):
            doc_error = f"Word-файл не знайдено ({docx_name}).\nМожливо, сервер перезапускався — створіть акт ще раз."
        else:
            try:
                pdf_path = build_act_pdf_path(
                    act["client_name"], act["contract_no"] or "", act_no
                )
                convert_to_pdf(docx_path, pdf_path)
                with get_db() as db:
                    db.execute(
                        "UPDATE acts SET pdf_path=? WHERE act_no=?",
                        (pdf_path, act_no)
                    )
                rel         = pdf_path[len(CONTRACTS_DIR):].lstrip("/\\")
                pdf_url_val = f"/docs/{rel}"
            except Exception as e:
                doc_error = str(e)

    return templates.TemplateResponse("acts/done.html", {
        "request":   request,
        "act_no":    act_no,
        "client":    act["client_name"] if act else "",
        "sum_uah":   float(act["sum_uah"] or 0) if act else 0,
        "docx_name": docx_name,
        "pdf_url":   pdf_url_val,
        "doc_error": doc_error,
    })


@router.get("/{act_no}/docx")
async def download_act_docx(act_no: str):
    """Serve the Word file from TMP_DIR."""
    fname = f"Акт_{act_no}.docx"
    path  = os.path.join(TMP_DIR, fname)
    if not os.path.exists(path):
        return HTMLResponse("Файл не знайдено (можливо, сервер перезапускався)", status_code=404)
    return FileResponse(path, filename=fname,
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@router.get("/{act_no}/edit", response_class=HTMLResponse)
async def edit_act_form(request: Request, act_no: str):
    with get_db() as db:
        act = db.execute("SELECT * FROM acts WHERE act_no=?", (act_no,)).fetchone()
    if not act:
        return HTMLResponse("Акт не знайдено", status_code=404)
    return templates.TemplateResponse("acts/edit.html", {
        "request": request,
        "act":     act,
        "title":   f"Редагувати {act_no}",
    })


@router.post("/{act_no}/edit")
async def edit_act(
    act_no:   str,
    act_date: str = Form(""),
    status:   str = Form("Чернетка"),
):
    with get_db() as db:
        db.execute(
            "UPDATE acts SET act_date=?, status=? WHERE act_no=?",
            (act_date or None, status, act_no)
        )
    return RedirectResponse("/acts", status_code=303)


@router.post("/{act_no}/delete")
async def delete_act(act_no: str):
    with get_db() as db:
        db.execute("DELETE FROM acts WHERE act_no=?", (act_no,))
    return RedirectResponse("/acts", status_code=303)


@router.post("/{act_no}/sign")
async def sign_act(act_no: str):
    with get_db() as db:
        db.execute("UPDATE acts SET status='Підписано' WHERE act_no=?", (act_no,))
    return RedirectResponse("/acts", status_code=303)


@router.post("/{act_no}/send")
async def send_act(act_no: str):
    with get_db() as db:
        db.execute("UPDATE acts SET status='Направлений' WHERE act_no=?", (act_no,))
    return RedirectResponse("/acts", status_code=303)


@router.post("/{act_no}/cancel")
async def cancel_act(act_no: str):
    with get_db() as db:
        db.execute("UPDATE acts SET status='Скасовано' WHERE act_no=?", (act_no,))
    return RedirectResponse("/acts", status_code=303)


# ── Відправити акт на email ───────────────────────────────────────

@router.post("/{act_no}/send-email")
async def send_act_email(act_no: str):
    """Надсилає PDF акту на email клієнта."""
    if not email_configured():
        return RedirectResponse("/acts?toast=nosmtp", status_code=303)

    with get_db() as db:
        act = db.execute(
            "SELECT * FROM acts WHERE act_no=?", (act_no,)
        ).fetchone()
        if not act:
            return RedirectResponse("/acts?toast=notfound", status_code=303)

        contract = db.execute(
            "SELECT * FROM contracts WHERE contract_no=?", (act["contract_no"],)
        ).fetchone() if act["contract_no"] else None

        client_emails_list = []
        if contract:
            ce = db.execute(
                "SELECT email, email_active, email2, email2_active, email3, email3_active "
                "FROM clients WHERE edrpou=?", (contract["edrpou"],)
            ).fetchone()
            client_emails_list = get_active_emails(ce) if ce else []

    if not client_emails_list:
        return RedirectResponse("/acts?toast=noemail", status_code=303)

    pdf_path = act["pdf_path"] or ""
    if not pdf_path or not os.path.exists(pdf_path):
        return RedirectResponse("/acts?toast=nopdf", status_code=303)

    sum_uah    = float(act["sum_uah"] or 0)
    to_display = ", ".join(client_emails_list)
    try:
        await send_email(
            to_email=client_emails_list,
            subject=f"Акт виконаних робіт {act_no}",
            body_html=body_act(
                act_no=act_no,
                client_name=act["client_name"] or "",
                sum_str=_app_fmt_money(sum_uah),
                our_name=EMAIL_FROM_NAME,
            ),
            attachment_path=pdf_path,
            attachment_name=f"Акт_{act_no}.pdf",
        )
        with get_db() as db:
            db.execute(
                "UPDATE acts SET email_sent_date=? WHERE act_no=?",
                (date.today().strftime("%d.%m.%Y"), act_no)
            )
        return RedirectResponse(f"/acts?toast=email_ok&to={to_display}", status_code=303)
    except Exception:
        return RedirectResponse("/acts?toast=email_error", status_code=303)


# ── Upload підписаного скану акту ────────────────────────────────

@router.post("/{act_no}/upload-scan")
async def upload_act_scan(
    act_no: str,
    scan_file: UploadFile = File(...),
):
    """Зберігає підписаний скан акту (PDF або фото)."""
    with get_db() as db:
        act = db.execute(
            "SELECT client_name, contract_no FROM acts WHERE act_no=?", (act_no,)
        ).fetchone()
    if not act:
        return HTMLResponse("Акт не знайдено", status_code=404)

    ext = Path(scan_file.filename).suffix.lower()
    if ext not in {".pdf", ".jpg", ".jpeg", ".png"}:
        return HTMLResponse(f"Формат {ext} не підтримується. Дозволено: PDF, JPG, PNG", status_code=400)

    from word_handler import _safe_folder_name
    client_folder   = _safe_folder_name(act["client_name"] or "")
    contract_folder = _safe_folder_name(act["contract_no"] or "")
    folder = os.path.join(CONTRACTS_DIR, client_folder, f"Договір {contract_folder}", "Акти")
    os.makedirs(folder, exist_ok=True)
    save_path = os.path.join(folder, f"Скан_{act_no}{ext}")

    content = await scan_file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    with get_db() as db:
        db.execute(
            "UPDATE acts SET scan_path=? WHERE act_no=?",
            (save_path, act_no)
        )
    return RedirectResponse("/acts?toast=scan_ok", status_code=303)
