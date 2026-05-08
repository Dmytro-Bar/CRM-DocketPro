"""Expenses router."""

from datetime import date
from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from database import get_db
from models import fmt_money, fmt_date, norm, parse_date, EXPENSE_CATEGORIES, make_xlsx

router = APIRouter(prefix="/expenses")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["fmt_money"] = fmt_money
templates.env.globals["fmt_date"]  = fmt_date


@router.get("", response_class=HTMLResponse)
async def list_expenses(
    request:   Request,
    date_from: str = Query(default=""),
    date_to:   str = Query(default=""),
    category:  str = Query(default=""),
):
    today = date.today()
    sql  = "SELECT * FROM expenses WHERE 1=1"
    args = []
    if date_from:
        sql += " AND exp_date >= ?"; args.append(date_from)
    if date_to:
        sql += " AND exp_date <= ?"; args.append(date_to)
    if category:
        sql += " AND category=?"; args.append(category)
    sql += " ORDER BY exp_date DESC"

    with get_db() as db:
        rows = db.execute(sql, args).fetchall()
        total = sum(float(r["amount_uah"] or 0) for r in rows)

    return templates.TemplateResponse("expenses.html", {
        "request":    request,
        "expenses":   rows,
        "total":      total,
        "categories": EXPENSE_CATEGORIES,
        "date_from":  date_from,
        "date_to":    date_to,
        "category":   category,
        "today_str":  fmt_date(today),
    })


@router.get("/export")
async def export_expenses(
    date_from: str = Query(default=""),
    date_to:   str = Query(default=""),
    category:  str = Query(default=""),
):
    sql  = "SELECT * FROM expenses WHERE 1=1"
    args = []
    if date_from:
        sql += " AND exp_date >= ?"; args.append(date_from)
    if date_to:
        sql += " AND exp_date <= ?"; args.append(date_to)
    if category:
        sql += " AND category=?"; args.append(category)
    sql += " ORDER BY exp_date DESC"

    with get_db() as db:
        rows = db.execute(sql, args).fetchall()

    headers = ["Дата", "Категорія", "Опис", "Сума", "Валюта", "Курс", "Сума (грн)"]
    data = [
        [r["exp_date"], r["category"], r["description"],
         float(r["amount"] or 0), r["currency"],
         float(r["exchange_rate"] or 1), float(r["amount_uah"] or 0)]
        for r in rows
    ]

    buf = make_xlsx(headers, data, "Витрати")
    filename = f"Витрати_{date.today().strftime('%d%m%Y')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"}
    )


@router.post("/new")
async def create_expense(
    exp_date:      str   = Form(...),
    category:      str   = Form(...),
    description:   str   = Form(""),
    amount:        float = Form(...),
    currency:      str   = Form("грн"),
    exchange_rate: float = Form(1),
):
    if currency == "грн":
        amount_uah = amount
    else:
        amount_uah = amount * exchange_rate

    with get_db() as db:
        db.execute(
            "INSERT INTO expenses (exp_date,category,description,amount,currency,exchange_rate,amount_uah) "
            "VALUES (?,?,?,?,?,?,?)",
            (exp_date, category, description.strip(), amount, currency, exchange_rate, round(amount_uah, 2))
        )
    return RedirectResponse("/expenses", status_code=303)


@router.post("/{expense_id}/delete")
async def delete_expense(expense_id: int):
    with get_db() as db:
        db.execute("DELETE FROM expenses WHERE id=?", (expense_id,))
    return RedirectResponse("/expenses", status_code=303)
