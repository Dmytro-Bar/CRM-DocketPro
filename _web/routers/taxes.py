"""Taxes router — Ukrainian FOP 3rd group tax reporting page."""

from datetime import datetime
from collections import defaultdict

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from database import get_db
from models import fmt_money, fmt_date

BASE_DIR   = Path(__file__).resolve().parent.parent
templates  = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["fmt_money"] = fmt_money
templates.env.globals["fmt_date"]  = fmt_date

router = APIRouter()

# ── helpers ───────────────────────────────────────────────────────────────

def _parse(date_str: str):
    """Parse dd.mm.yyyy → datetime.date or None."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), "%d.%m.%Y").date()
    except ValueError:
        return None


def _quarter(d) -> int:
    """Return quarter number (1-4) for a date."""
    return (d.month - 1) // 3 + 1


# Tax rates
EP_RATE  = 0.05   # 5% єдиний податок
VZ_RATE  = 0.01   # 1% військовий збір

# Period labels and cumulative month ranges (end month inclusive)
PERIODS = [
    (1, "I квартал",   1, 3),
    (2, "Півріччя",    1, 6),
    (3, "9 місяців",   1, 9),
    (4, "Рік",         1, 12),
]


# ── data computation ──────────────────────────────────────────────────────

def _compute_tax_data() -> dict:
    """
    Returns dict: {year: [period_rows]} sorted year desc.

    period_row keys:
      period_no, label, quarter_income, cumulative_income,
      ep_cumulative, vz_cumulative, total_tax_cumulative,
      ep_quarter, vz_quarter, total_tax_quarter
    """
    # ── collect all income entries ────────────────────────────────────────
    with get_db() as db:
        paid_invoices = db.execute(
            "SELECT pay_date, sum_uah FROM invoices "
            "WHERE pay_status = 'Оплачено' AND pay_date IS NOT NULL AND pay_date != ''"
        ).fetchall()

        app_payments = db.execute(
            "SELECT pay_date, amount, source FROM app_payments "
            "WHERE pay_date IS NOT NULL AND pay_date != ''"
        ).fetchall()

    # LiqPay deducts 1.5% commission before crediting the account.
    # For tax purposes we need the gross amount the client actually paid.
    LYQPAY_COMMISSION = 0.015   # 1.5%

    def _gross(amount: float, source: str) -> float:
        """Return gross (pre-commission) income for tax purposes."""
        if (source or "").strip().lower() in ("lyqpay", "liqpay"):
            return amount / (1 - LYQPAY_COMMISSION)   # e.g. 3181.55 / 0.985 ≈ 3230.00
        return amount

    # year → month (1-12) → total income
    income: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))

    for row in paid_invoices:
        d = _parse(row["pay_date"])
        if d:
            income[d.year][d.month] += float(row["sum_uah"] or 0)

    for row in app_payments:
        d = _parse(row["pay_date"])
        if d:
            gross = _gross(float(row["amount"] or 0), row["source"] or "")
            income[d.year][d.month] += gross

    if not income:
        return {}

    result = {}
    for year in sorted(income.keys(), reverse=True):
        months = income[year]

        # quarter incomes
        q_inc = [0.0] * 5  # index 1-4
        for month, amt in months.items():
            q = _quarter_from_month(month)
            q_inc[q] += amt

        rows = []
        cumul = 0.0
        for period_no, label, _m_from, m_to in PERIODS:
            # quarter income = sum of months in this quarter only
            q = period_no  # quarter number == period_no
            q_income = q_inc[q]
            cumul += q_income

            ep_q   = round(q_income * EP_RATE, 2)
            vz_q   = round(q_income * VZ_RATE, 2)
            ttq    = round(ep_q + vz_q, 2)

            ep_c   = round(cumul * EP_RATE, 2)
            vz_c   = round(cumul * VZ_RATE, 2)
            ttc    = round(ep_c + vz_c, 2)

            rows.append({
                "period_no":            period_no,
                "label":                label,
                "quarter_income":       round(q_income, 2),
                "cumulative_income":    round(cumul, 2),
                "ep_quarter":           ep_q,
                "vz_quarter":           vz_q,
                "total_tax_quarter":    ttq,
                "ep_cumulative":        ep_c,
                "vz_cumulative":        vz_c,
                "total_tax_cumulative": ttc,
            })

        result[year] = rows

    return result


def _quarter_from_month(month: int) -> int:
    return (month - 1) // 3 + 1


# ── route ─────────────────────────────────────────────────────────────────

@router.get("/taxes", response_class=HTMLResponse)
async def taxes_page(request: Request):
    tax_data = _compute_tax_data()
    years    = sorted(tax_data.keys(), reverse=True)

    # active year: default to current year if present, else latest
    current_year = datetime.now().year
    active_year  = current_year if current_year in tax_data else (years[0] if years else current_year)

    return templates.TemplateResponse("taxes.html", {
        "request":     request,
        "tax_data":    tax_data,
        "years":       years,
        "active_year": active_year,
        "ep_rate":     EP_RATE,
        "vz_rate":     VZ_RATE,
    })
