"""LiqPay router — direct integration with LiqPay payment register API."""

from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from models import fmt_money, fmt_date, parse_date
import liqpay_client as lc

router = APIRouter(prefix="/lyqpay")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["fmt_money"]     = fmt_money
templates.env.globals["fmt_date"]      = fmt_date
templates.env.globals["status_label"]  = lc.status_label


@router.get("", response_class=HTMLResponse)
async def lyqpay_page(request: Request):
    today     = date.today()
    date_from = (today - timedelta(days=30)).strftime("%d.%m.%Y")
    date_to   = today.strftime("%d.%m.%Y")
    return templates.TemplateResponse("lyqpay.html", {
        "request":    request,
        "payments":   [],
        "total":      0.0,
        "commission": 0.0,
        "date_from":  date_from,
        "date_to":    date_to,
        "error":      "",
        "loaded":     False,
        "configured": lc.configured(),
        "debug":      [],
    })


@router.post("/fetch", response_class=HTMLResponse)
async def fetch_payments(
    request:   Request,
    date_from: str = Form(...),
    date_to:   str = Form(...),
):
    df = parse_date(date_from)
    dt = parse_date(date_to)

    if not df or not dt:
        return templates.TemplateResponse("lyqpay.html", {
            "request":    request,
            "payments":   [],
            "total":      0.0,
            "commission": 0.0,
            "date_from":  date_from,
            "date_to":    date_to,
            "error":      "Невірний формат дати. Використовуйте дд.мм.рррр",
            "loaded":     False,
            "configured": lc.configured(),
        })

    if dt < df:
        df, dt = dt, df

    try:
        payments, debug = lc.fetch_payments(df, dt)

        total      = sum(p["amount"]     for p in payments)
        commission = sum(p["commission"] for p in payments)

        return templates.TemplateResponse("lyqpay.html", {
            "request":    request,
            "payments":   payments,
            "total":      total,
            "commission": commission,
            "date_from":  date_from,
            "date_to":    date_to,
            "error":      "",
            "loaded":     True,
            "configured": True,
            "debug":      debug,
        })

    except Exception as e:
        return templates.TemplateResponse("lyqpay.html", {
            "request":    request,
            "payments":   [],
            "total":      0.0,
            "commission": 0.0,
            "date_from":  date_from,
            "date_to":    date_to,
            "error":      str(e),
            "loaded":     False,
            "configured": lc.configured(),
            "debug":      [],
        })
