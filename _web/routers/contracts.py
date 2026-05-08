"""Contracts CRUD router."""

from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from datetime import date
from urllib.parse import quote

from database import get_db
from models import fmt_money, fmt_date, norm, parse_date, make_xlsx

router = APIRouter(prefix="/contracts")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["fmt_money"] = fmt_money
templates.env.globals["fmt_date"]  = fmt_date
templates.env.globals["norm"]      = norm

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

    return templates.TemplateResponse("contracts.html", {
        "request":      request,
        "contracts":    contracts,
        "ctype":        ctype,
        "q":            q,
        "counts":       counts,
        "mrr":          mrr,
        "arr":          mrr * 12,
        "active_cnt":   active_cnt,
        "hourly_cnt":   hourly_cnt,
        "total_debt":   total_debt,
        "today":        today,
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
    })


@router.post("/new")
async def create_contract(
    contract_no:   str   = Form(...),
    edrpou:        str   = Form(...),
    contract_date: str   = Form(""),
    contract_end:  str   = Form(""),
    currency:      str   = Form("UAH"),
    tariff_fx:     float = Form(0),
    type_rate:     str   = Form(""),
    users:         int   = Form(1),
    status:        str   = Form("Активний"),
    contract_type: str   = Form("Доступ"),
    subject:       str   = Form(""),
    hour_rate:     float = Form(0),
    nbu_rate:      float = Form(0),
    act_template:  str   = Form(""),
):
    with get_db() as db:
        client_name = _client_name_for(db, edrpou)
        db.execute(
            "INSERT OR IGNORE INTO contracts "
            "(contract_no,edrpou,client_name,contract_date,contract_end,currency,"
            "tariff_fx,type_rate,users,status,contract_type,subject,hour_rate,nbu_rate,act_template) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (contract_no, edrpou, client_name, contract_date, contract_end, currency,
             tariff_fx, type_rate, users, status, contract_type, subject,
             hour_rate, nbu_rate, act_template)
        )
    return RedirectResponse("/contracts", status_code=303)


@router.get("/{contract_no}/edit", response_class=HTMLResponse)
async def edit_contract_form(contract_no: str, request: Request):
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
    contract_no:   str,
    edrpou:        str   = Form(...),
    contract_date: str   = Form(""),
    contract_end:  str   = Form(""),
    currency:      str   = Form("UAH"),
    tariff_fx:     float = Form(0),
    type_rate:     str   = Form(""),
    users:         int   = Form(1),
    status:        str   = Form("Активний"),
    contract_type: str   = Form("Доступ"),
    subject:       str   = Form(""),
    hour_rate:     float = Form(0),
    nbu_rate:      float = Form(0),
    act_template:  str   = Form(""),
):
    with get_db() as db:
        client_name = _client_name_for(db, edrpou)
        db.execute(
            "UPDATE contracts SET edrpou=?,client_name=?,contract_date=?,"
            "contract_end=?,currency=?,tariff_fx=?,type_rate=?,users=?,status=?,"
            "contract_type=?,subject=?,hour_rate=?,nbu_rate=?,act_template=? "
            "WHERE contract_no=?",
            (edrpou, client_name, contract_date, contract_end, currency,
             tariff_fx, type_rate, users, status, contract_type, subject,
             hour_rate, nbu_rate, act_template, contract_no)
        )
    return RedirectResponse("/contracts", status_code=303)
