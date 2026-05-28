"""Contracts CRUD router."""

import os
import shutil
from fastapi import APIRouter, Request, Form, Query, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from typing import Optional

from datetime import date
from urllib.parse import quote

from database import get_db
from models import fmt_money, fmt_date, norm, parse_date, make_xlsx, pdf_url
from config import CONTRACTS_DIR
import nbu_client

router = APIRouter(prefix="/contracts")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["fmt_money"] = fmt_money
templates.env.globals["fmt_date"]  = fmt_date
templates.env.globals["norm"]      = norm
templates.env.globals["pdf_url"]   = pdf_url


# ── Upload helpers ─────────────────────────────────────────────
_ALLOWED_SCAN_EXT = {".pdf", ".jpg", ".jpeg", ".png"}

def _safe_folder(name: str) -> str:
    forbidden = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
    r = name
    for ch in forbidden:
        r = r.replace(ch, "_")
    return r.strip()

def _contract_scan_path(client_name: str, contract_no: str, ext: str) -> str:
    folder = os.path.join(
        CONTRACTS_DIR,
        _safe_folder(client_name),
        f"Договір {_safe_folder(contract_no)}",
    )
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"Скан_договору{ext}")

# ── Act template helpers ───────────────────────────────────────
_TMPL_DIR = Path(__file__).resolve().parent.parent.parent / "_templates"

def _act_templates() -> list[str]:
    if not _TMPL_DIR.exists():
        return []
    return sorted(f.name for f in _TMPL_DIR.glob("*.docx") if "Акт" in f.name)

def _client_name_for(db, edrpou: str) -> str:
    row = db.execute("SELECT name FROM clients WHERE edrpou=?", (edrpou,)).fetchone()
    return row["name"] if row else ""


# ── Main query with debt/stats subqueries ──────────────────────
_LIST_SQL = """
SELECT c.*,
    COALESCE((
        SELECT SUM(i.sum_uah) FROM invoices i
        WHERE i.contract_no = c.contract_no
          AND i.pay_status NOT IN ('Оплачено','Скасовано')
    ), 0) AS debt,
    COALESCE((
        SELECT COUNT(*) FROM invoices i WHERE i.contract_no = c.contract_no
    ), 0) AS inv_total,
    COALESCE((
        SELECT COUNT(*) FROM invoices i
        WHERE i.contract_no = c.contract_no
          AND i.pay_status NOT IN ('Оплачено','Скасовано')
    ), 0) AS inv_unpaid,
    COALESCE((
        SELECT COUNT(*) FROM acts a WHERE a.contract_no = c.contract_no
    ), 0) AS act_total
FROM contracts c
WHERE 1=1
"""


@router.get("", response_class=HTMLResponse)
async def list_contracts(
    request: Request,
    ctype: str = Query(default=""),
    q:     str = Query(default=""),
):
    sql  = _LIST_SQL
    args = []
    if ctype:
        sql += " AND c.contract_type=?"; args.append(ctype)
    if q:
        sql += " AND (c.client_name LIKE ? OR c.contract_no LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY c.contract_no"

    today = date.today()
    with get_db() as db:
        rows = db.execute(sql, args).fetchall()

        # Build last_inv_period: contract_no → invoice with max period_to date.
        # Must compare as Python date objects (period_to stored as dd.mm.yyyy string).
        inv_rows = db.execute(
            "SELECT contract_no, period_from, period_to, sum_uah, months "
            "FROM invoices WHERE pay_status != 'Скасовано' "
            "AND period_to IS NOT NULL AND period_to != ''"
        ).fetchall()

    last_inv_period: dict = {}
    for ir in inv_rows:
        cno = ir["contract_no"] or ""
        pto = parse_date(ir["period_to"])
        if not pto or not cno:
            continue
        existing = last_inv_period.get(cno)
        if not existing or pto > existing["to"]:
            last_inv_period[cno] = {
                "to":      pto,
                "from":    parse_date(ir["period_from"]),
                "sum_uah": float(ir["sum_uah"] or 0),
                "months":  int(ir["months"] or 1),
            }

    contracts   = []
    mrr         = 0.0
    total_debt  = 0.0
    active_cnt  = 0
    hourly_cnt  = 0
    counts      = {"all": 0, "active": 0, "paused": 0, "ended": 0,
                   "draft": 0, "expiring": 0}

    for r in rows:
        end_date  = parse_date(r["contract_end"])
        start_date = parse_date(r["contract_date"])
        days_left = (end_date - today).days if end_date else None
        st        = norm(r["status"] or "")
        ctype_r   = norm(r["contract_type"] or "")
        debt      = float(r["debt"] or 0)

        expiring = (
            st == "Активний"
            and days_left is not None
            and 0 <= days_left <= 30
        )

        # Totals for active contracts
        if st == "Активний":
            active_cnt += 1
            total_debt += debt
            if ctype_r == "Доступ":
                info = last_inv_period.get(r["contract_no"])
                if info:
                    # Same logic as dashboard: sum_uah of latest-period invoice ÷ months
                    pf, pt = info["from"], info["to"]
                    sv = info["sum_uah"]
                    if pf and pt:
                        mc = (pt.year * 12 + pt.month) - (pf.year * 12 + pf.month) + 1
                        mc = max(1, mc)
                    else:
                        mc = info["months"] or 1
                    mrr += sv / mc
                else:
                    # No invoice yet — fall back to tariff × users (UAH only)
                    if (r["currency"] or "UAH") == "UAH":
                        mrr += float(r["tariff_fx"] or 0) * int(r["users"] or 1)
            else:
                hourly_cnt += 1

        # Tab counts
        counts["all"] += 1
        if st == "Активний":
            counts["active"] += 1
        elif st == "Призупинено":
            counts["paused"] += 1
        elif st in ("Завершено", "Закінчено"):
            counts["ended"] += 1
        else:
            counts["draft"] += 1
        if expiring:
            counts["expiring"] += 1

        contracts.append({
            "row":        r,
            "days_left":  days_left,
            "st":         st,
            "ctype_r":    ctype_r,
            "expiring":   expiring,
            "debt":       debt,
            "inv_total":  int(r["inv_total"] or 0),
            "inv_unpaid": int(r["inv_unpaid"] or 0),
            "act_total":  int(r["act_total"] or 0),
            "sum_month":  float(r["tariff_fx"] or 0) * int(r["users"] or 1)
                          if ctype_r == "Доступ" else 0,
        })

    # NBU alert set — contract numbers currently over threshold
    nbu_result = nbu_client.compute_alerts([r for r in rows])
    nbu_alert_nos = {a["contract_no"] for a in nbu_result["alerts"]}

    return templates.TemplateResponse("contracts.html", {
        "request":       request,
        "contracts":     contracts,
        "ctype":         ctype,
        "q":             q,
        "counts":        counts,
        "mrr":           mrr,
        "arr":           mrr * 12,
        "active_cnt":    active_cnt,
        "hourly_cnt":    hourly_cnt,
        "total_debt":    total_debt,
        "today":         today,
        "nbu_alert_nos": nbu_alert_nos,
        "nbu_rate":      nbu_result["current_rate"],
        "nbu_rate_date": nbu_result["rate_date"],
    })


@router.get("/export")
async def export_contracts(
    ctype: str = Query(default=""),
    q:     str = Query(default=""),
):
    sql  = _LIST_SQL
    args = []
    if ctype:
        sql += " AND c.contract_type=?"; args.append(ctype)
    if q:
        sql += " AND (c.client_name LIKE ? OR c.contract_no LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY c.contract_no"

    with get_db() as db:
        rows = db.execute(sql, args).fetchall()

    headers = ["№ договору", "Клієнт", "ЄДРПОУ", "Тип", "Валюта", "Тариф",
               "Погодинна ставка", "Користувачів", "Статус", "Дата договору",
               "Дійсний до", "Борг (грн)", "Рахунків", "Актів"]
    data = []
    for r in rows:
        data.append([
            r["contract_no"], r["client_name"], r["edrpou"],
            norm(r["contract_type"]), r["currency"],
            float(r["tariff_fx"] or 0), float(r["hour_rate"] or 0),
            int(r["users"] or 1), norm(r["status"]),
            r["contract_date"], r["contract_end"],
            float(r["debt"] or 0), int(r["inv_total"] or 0), int(r["act_total"] or 0),
        ])

    buf = make_xlsx(headers, data, "Договори")
    fn  = f"Договори_{date.today().strftime('%d%m%Y')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fn)}"}
    )


@router.get("/new", response_class=HTMLResponse)
async def new_contract_form(request: Request):
    with get_db() as db:
        clients = db.execute("SELECT edrpou, name FROM clients ORDER BY name").fetchall()
    return templates.TemplateResponse("contract_form.html", {
        "request": request, "contract": None, "clients": clients,
        "act_templates": _act_templates(), "title": "Новий договір",
        "contracts_dir": CONTRACTS_DIR, "scan_uploaded": False,
    })


@router.post("/new")
async def create_contract(
    contract_no:        str   = Form(...),
    edrpou:             str   = Form(...),
    contract_date:      str   = Form(""),
    contract_end:       str   = Form(""),
    currency:           str   = Form("UAH"),
    tariff_fx:          float = Form(0),
    type_rate:          str   = Form(""),
    users:              int   = Form(1),
    status:             str   = Form("Активний"),
    contract_type:      str   = Form("Доступ"),
    subject:            str   = Form(""),
    hour_rate:          float = Form(0),
    nbu_rate:           float = Form(0),
    nbu_tracking:       int   = Form(0),
    nbu_threshold_pct:  float = Form(5.0),
    act_template:       str   = Form(""),
    notes:              str   = Form(""),
):
    with get_db() as db:
        client_name = _client_name_for(db, edrpou)
        db.execute(
            "INSERT OR IGNORE INTO contracts "
            "(contract_no,edrpou,client_name,contract_date,contract_end,currency,"
            "tariff_fx,type_rate,users,status,contract_type,subject,hour_rate,"
            "nbu_rate,nbu_tracking,nbu_threshold_pct,act_template,notes) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (contract_no, edrpou, client_name, contract_date, contract_end, currency,
             tariff_fx, type_rate, users, status, contract_type, subject,
             hour_rate, nbu_rate, nbu_tracking, nbu_threshold_pct, act_template, notes[:500])
        )
    return RedirectResponse("/contracts", status_code=303)


@router.get("/{contract_no}/edit", response_class=HTMLResponse)
async def edit_contract_form(contract_no: str, request: Request, scan: str = ""):
    with get_db() as db:
        contract = db.execute(
            "SELECT * FROM contracts WHERE contract_no=?", (contract_no,)
        ).fetchone()
        clients = db.execute("SELECT edrpou, name FROM clients ORDER BY name").fetchall()
    if not contract:
        return HTMLResponse("Договір не знайдено", status_code=404)
    return templates.TemplateResponse("contract_form.html", {
        "request": request, "contract": contract, "clients": clients,
        "act_templates": _act_templates(), "title": f"Договір {contract_no}",
        "contracts_dir": CONTRACTS_DIR,
        "scan_uploaded": scan == "ok",
    })


@router.post("/{contract_no}/delete")
async def delete_contract(contract_no: str):
    with get_db() as db:
        invoice_nos = [
            r["invoice_no"]
            for r in db.execute(
                "SELECT invoice_no FROM invoices WHERE contract_no=?", (contract_no,)
            ).fetchall()
        ]
        for inv_no in invoice_nos:
            db.execute("DELETE FROM acts     WHERE invoice_no=?", (inv_no,))
        db.execute("DELETE FROM invoices  WHERE contract_no=?", (contract_no,))
        db.execute("DELETE FROM contracts WHERE contract_no=?", (contract_no,))
    return RedirectResponse("/contracts", status_code=303)


@router.post("/{contract_no}/edit")
async def update_contract(
    contract_no:        str,
    edrpou:             str   = Form(...),
    contract_date:      str   = Form(""),
    contract_end:       str   = Form(""),
    currency:           str   = Form("UAH"),
    tariff_fx:          float = Form(0),
    type_rate:          str   = Form(""),
    users:              int   = Form(1),
    status:             str   = Form("Активний"),
    contract_type:      str   = Form("Доступ"),
    subject:            str   = Form(""),
    hour_rate:          float = Form(0),
    nbu_rate:           float = Form(0),
    nbu_tracking:       int   = Form(0),
    nbu_threshold_pct:  float = Form(5.0),
    act_template:       str   = Form(""),
    notes:              str   = Form(""),
):
    with get_db() as db:
        client_name = _client_name_for(db, edrpou)
        db.execute(
            "UPDATE contracts SET edrpou=?,client_name=?,contract_date=?,"
            "contract_end=?,currency=?,tariff_fx=?,type_rate=?,users=?,status=?,"
            "contract_type=?,subject=?,hour_rate=?,nbu_rate=?,nbu_tracking=?,"
            "nbu_threshold_pct=?,act_template=?,notes=? "
            "WHERE contract_no=?",
            (edrpou, client_name, contract_date, contract_end, currency,
             tariff_fx, type_rate, users, status, contract_type, subject,
             hour_rate, nbu_rate, nbu_tracking, nbu_threshold_pct,
             act_template, notes[:500], contract_no)
        )
    return RedirectResponse("/contracts", status_code=303)


@router.post("/{contract_no}/upload-scan")
async def upload_contract_scan(
    contract_no: str,
    scan_file: UploadFile = File(...),
):
    """Завантажує підписаний скан договору."""
    with get_db() as db:
        contract = db.execute(
            "SELECT client_name FROM contracts WHERE contract_no=?", (contract_no,)
        ).fetchone()
    if not contract:
        return HTMLResponse("Договір не знайдено", status_code=404)

    ext = Path(scan_file.filename).suffix.lower()
    if ext not in _ALLOWED_SCAN_EXT:
        return HTMLResponse(f"Формат {ext} не підтримується. Дозволено: PDF, JPG, PNG", status_code=400)

    save_path = _contract_scan_path(contract["client_name"], contract_no, ext)
    content = await scan_file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    with get_db() as db:
        db.execute(
            "UPDATE contracts SET scan_path=? WHERE contract_no=?",
            (save_path, contract_no)
        )
    return RedirectResponse(f"/contracts/{contract_no}/edit?scan=ok", status_code=303)


# ── Contract Amendments (ДУ) ──────────────────────────────────────────────

def _amendment_scan_path(client_name: str, contract_no: str, du_no: str, ext: str) -> str:
    folder = os.path.join(
        CONTRACTS_DIR,
        _safe_folder(client_name),
        f"Договір {_safe_folder(contract_no)}",
        "Додаткові угоди",
    )
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"ДУ_{_safe_folder(du_no)}{ext}")


@router.get("/{contract_no}/amendments", response_class=HTMLResponse)
async def amendments_partial(request: Request, contract_no: str):
    """HTMX partial: list of amendments for a contract."""
    with get_db() as db:
        amendments = db.execute(
            "SELECT * FROM contract_amendments "
            "WHERE contract_no=? ORDER BY effective_date, id",
            (contract_no,)
        ).fetchall()
        contract = db.execute(
            "SELECT client_name, contract_type, currency FROM contracts WHERE contract_no=?",
            (contract_no,)
        ).fetchone()

    return templates.TemplateResponse("partials/amendments_list.html", {
        "request":     request,
        "amendments":  amendments,
        "contract_no": contract_no,
        "contract":    contract,
    })


@router.post("/{contract_no}/amendments/add")
async def add_amendment(
    request:        Request,
    contract_no:    str,
    du_no:          str          = Form(...),
    sign_date:      str          = Form(""),
    effective_date: str          = Form(""),
    users:          str          = Form(""),       # empty string = not changed
    tariff_fx:      str          = Form(""),
    contract_end:   str          = Form(""),
    notes:          str          = Form(""),
    du_scan:        UploadFile   = File(None),
    update_contract: int         = Form(0),        # 1 = also update contract fields
):
    users_val     = int(users)     if users.strip()     else None
    tariff_val    = float(tariff_fx) if tariff_fx.strip() else None
    end_val       = contract_end.strip() or None

    # Determine whether this amendment is already in effect
    today = date.today()
    eff_date = None
    if effective_date.strip():
        try:
            from datetime import datetime
            eff_date = datetime.strptime(effective_date.strip(), "%d.%m.%Y").date()
        except ValueError:
            pass

    # Handle ДУ scan upload
    pdf_path = None
    if du_scan and du_scan.filename:
        ext = Path(du_scan.filename).suffix.lower()
        if ext in _ALLOWED_SCAN_EXT:
            with get_db() as db:
                contract = db.execute(
                    "SELECT client_name FROM contracts WHERE contract_no=?",
                    (contract_no,)
                ).fetchone()
            if contract:
                save_path = _amendment_scan_path(
                    contract["client_name"], contract_no, du_no, ext
                )
                content = await du_scan.read()
                with open(save_path, "wb") as f:
                    f.write(content)
                pdf_path = save_path

    with get_db() as db:
        db.execute(
            "INSERT INTO contract_amendments "
            "(contract_no, du_no, sign_date, effective_date, users, tariff_fx, contract_end, notes, pdf_path) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (contract_no, du_no.strip(), sign_date.strip() or None,
             effective_date.strip() or None,
             users_val, tariff_val, end_val, notes.strip() or None, pdf_path)
        )

        # Auto-update contract if requested OR if effective_date is today or past
        should_update = bool(update_contract) or (eff_date is not None and eff_date <= today)
        if should_update:
            parts, vals = [], []
            if users_val is not None:
                parts.append("users=?");    vals.append(users_val)
            if tariff_val is not None:
                parts.append("tariff_fx=?"); vals.append(tariff_val)
            if end_val:
                parts.append("contract_end=?"); vals.append(end_val)
            if parts:
                vals.append(contract_no)
                db.execute(f"UPDATE contracts SET {', '.join(parts)} WHERE contract_no=?", vals)

    return RedirectResponse(f"/contracts?highlight={contract_no}", status_code=303)


@router.post("/amendments/{amendment_id}/delete")
async def delete_amendment(amendment_id: int):
    with get_db() as db:
        row = db.execute(
            "SELECT contract_no, pdf_path FROM contract_amendments WHERE id=?",
            (amendment_id,)
        ).fetchone()
        if row:
            if row["pdf_path"] and os.path.exists(row["pdf_path"]):
                try:
                    os.remove(row["pdf_path"])
                except OSError:
                    pass
            db.execute("DELETE FROM contract_amendments WHERE id=?", (amendment_id,))
            contract_no = row["contract_no"]

    return RedirectResponse(f"/contracts?highlight={contract_no}", status_code=303)
