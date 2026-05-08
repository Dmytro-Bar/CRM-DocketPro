"""Clients CRUD router."""

from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from database import get_db
from models import fmt_money, fmt_date, norm, make_xlsx

router = APIRouter(prefix="/clients")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["fmt_money"] = fmt_money
templates.env.globals["fmt_date"]  = fmt_date
templates.env.globals["norm"]      = norm


@router.get("", response_class=HTMLResponse)
async def list_clients(request: Request, q: str = ""):
    with get_db() as db:
        if q:
            rows = db.execute(
                "SELECT * FROM clients WHERE name LIKE ? OR edrpou LIKE ? ORDER BY name",
                (f"%{q}%", f"%{q}%")
            ).fetchall()
        else:
            rows = db.execute("SELECT * FROM clients ORDER BY name").fetchall()
    return templates.TemplateResponse("clients.html", {
        "request": request, "clients": rows, "q": q
    })


@router.get("/export")
async def export_clients(q: str = Query(default="")):
    from datetime import date
    sql  = "SELECT * FROM clients WHERE 1=1"
    args = []
    if q:
        sql += " AND (name LIKE ? OR edrpou LIKE ?)"; args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY name"

    with get_db() as db:
        rows = db.execute(sql, args).fetchall()

    headers = ["ЄДРПОУ", "Назва", "Директор", "Email", "Телефон", "Адреса", "Статус"]
    data = [
        [r["edrpou"], r["name"], r["director"], r["email"],
         r["phone"], r["address"], norm(r["status"])]
        for r in rows
    ]

    buf = make_xlsx(headers, data, "Клієнти")
    filename = f"Клієнти_{date.today().strftime('%d%m%Y')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"}
    )


@router.get("/new", response_class=HTMLResponse)
async def new_client_form(request: Request):
    return templates.TemplateResponse("client_form.html", {
        "request": request, "client": None, "title": "Новий клієнт"
    })


@router.post("/new")
async def create_client(
    request: Request,
    edrpou:   str = Form(...),
    name:     str = Form(...),
    director: str = Form(""),
    email:    str = Form(""),
    phone:    str = Form(""),
    address:  str = Form(""),
    status:   str = Form("Активний"),
):
    with get_db() as db:
        db.execute(
            "INSERT OR IGNORE INTO clients (edrpou, name, director, email, phone, address, status) "
            "VALUES (?,?,?,?,?,?,?)",
            (edrpou.strip(), name.strip(), director.strip(), email.strip(),
             phone.strip(), address.strip(), status)
        )
    return RedirectResponse("/clients", status_code=303)


@router.get("/{edrpou}/edit", response_class=HTMLResponse)
async def edit_client_form(edrpou: str, request: Request):
    with get_db() as db:
        client = db.execute("SELECT * FROM clients WHERE edrpou=?", (edrpou,)).fetchone()
    if not client:
        return HTMLResponse("Клієнта не знайдено", status_code=404)
    return templates.TemplateResponse("client_form.html", {
        "request": request, "client": client, "title": "Редагувати клієнта"
    })


@router.post("/{edrpou}/delete")
async def delete_client(edrpou: str):
    with get_db() as db:
        # cascade: acts → invoices → contracts → client
        contract_nos = [
            r["contract_no"]
            for r in db.execute(
                "SELECT contract_no FROM contracts WHERE edrpou=?", (edrpou,)
            ).fetchall()
        ]
        for cn in contract_nos:
            invoice_nos = [
                r["invoice_no"]
                for r in db.execute(
                    "SELECT invoice_no FROM invoices WHERE contract_no=?", (cn,)
                ).fetchall()
            ]
            for inv_no in invoice_nos:
                db.execute("DELETE FROM acts     WHERE invoice_no=?",  (inv_no,))
            db.execute("DELETE FROM invoices WHERE contract_no=?", (cn,))
        db.execute("DELETE FROM contracts WHERE edrpou=?", (edrpou,))
        db.execute("DELETE FROM clients   WHERE edrpou=?", (edrpou,))
    return RedirectResponse("/clients", status_code=303)


@router.post("/{edrpou}/edit")
async def update_client(
    edrpou: str,
    name:     str = Form(...),
    director: str = Form(""),
    email:    str = Form(""),
    phone:    str = Form(""),
    address:  str = Form(""),
    status:   str = Form("Активний"),
):
    with get_db() as db:
        db.execute(
            "UPDATE clients SET name=?, director=?, email=?, phone=?, address=?, status=? "
            "WHERE edrpou=?",
            (name.strip(), director.strip(), email.strip(), phone.strip(),
             address.strip(), status, edrpou)
        )
    return RedirectResponse("/clients", status_code=303)
