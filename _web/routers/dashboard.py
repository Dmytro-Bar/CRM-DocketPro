"""
Dashboard router — KPI calculation from SQLite.
Mirrors the business logic of dashboard_app.py → load_data().
"""

import calendar
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from database import get_db
from models import norm, parse_date, fmt_date, fmt_money, is_cancelled
import nbu_client

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["fmt_money"] = fmt_money
templates.env.globals["fmt_date"]  = fmt_date
templates.env.globals["norm"]      = norm


def _month_start(d: date) -> date:
    return d.replace(day=1)

def _month_end(d: date) -> date:
    last = calendar.monthrange(d.year, d.month)[1]
    return d.replace(day=last)

def _next_month_start(d: date) -> date:
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def compute_kpi(date_from: date, date_to: date,
                client_filter: str = "", ctype_filter: str = "") -> dict:
    today  = date.today()
    ALL_C  = not client_filter
    ALL_T  = not ctype_filter

    with get_db() as db:
        # ── Contracts ─────────────────────────────────────────
        rows_c = db.execute("SELECT * FROM contracts").fetchall()
        cno_to_ctype  = {}
        cno_to_client = {}
        ok_cnos       = set()
        contracts_active  = 0
        total_users       = 0
        all_clients       = set()
        contracts_list    = []
        all_client_names  = sorted(set(
            r["client_name"] for r in rows_c if r["client_name"]
        ))

        for r in rows_c:
            cno    = r["contract_no"]
            status = norm(r["status"])
            client = r["client_name"] or ""
            ctype  = norm(r["contract_type"]) or "Доступ"
            cno_to_ctype[cno]  = ctype
            cno_to_client[cno] = client

            match_c = ALL_C or (client_filter in client)
            match_t = ALL_T or (ctype_filter == ctype)

            if match_c and match_t:
                ok_cnos.add(cno)
                if status == "Активний":
                    contracts_active += 1
                    total_users += int(r["users"] or 0)
                    all_clients.add(client)

            contracts_list.append({
                "no":     cno,
                "client": client,
                "ctype":  ctype,
                "status": status,
                "ok":     match_c and match_t,
                "curr":   r["currency"] or "UAH",
                "tariff": float(r["tariff_fx"] or 0),
            })

        clients_active = len(all_clients)

        # ── Invoices ──────────────────────────────────────────
        rows_i = db.execute("SELECT * FROM invoices").fetchall()

        inv_period              = 0.0
        paid_period             = 0.0
        debt_real               = 0.0
        debt_pending            = 0.0
        access_revenue_all_time = 0.0
        # Track last invoice period per contract: {cno: {"from": date, "to": date}}
        last_inv_period         = {}
        all_invoices            = []
        paid_invoices           = []

        for r in rows_i:
            cno      = r["contract_no"] or ""
            client   = r["client_name"] or ""
            inv_no   = r["invoice_no"]  or ""
            inv_date = parse_date(r["invoice_date"])
            sum_uah  = float(r["sum_uah"] or 0)
            pfrom    = parse_date(r["period_from"])
            pto      = parse_date(r["period_to"])
            due      = parse_date(r["due_date"])
            status   = norm(r["pay_status"])
            pay_date = parse_date(r["pay_date"])
            inv_type = norm(r["invoice_type"] or "")

            # Filter by contract
            if cno in cno_to_ctype:
                if cno not in ok_cnos:
                    continue
            else:
                if not (ALL_C or (client_filter and client_filter in client)):
                    continue

            if status == "Скасовано":
                continue

            if pto and cno:
                existing = last_inv_period.get(cno)
                if not existing or pto > existing["to"]:
                    last_inv_period[cno] = {"from": pfrom, "to": pto, "sum_uah": sum_uah}

            # A) Invoiced in period
            if inv_date and date_from <= inv_date <= date_to:
                inv_period += sum_uah

            # B) Paid in period
            if pay_date and date_from <= pay_date <= date_to:
                paid_period += sum_uah
                paid_invoices.append({
                    "inv_no":   inv_no,
                    "client":   client,
                    "contract": cno,
                    "sum_uah":  sum_uah,
                    "pay_date": pay_date,
                    "inv_date": inv_date,
                })

            # C/D) Debt / pending — always current snapshot
            eff_due = due or (inv_date + timedelta(days=30) if inv_date else None)
            overdue_flag  = (status != "Оплачено") and bool(eff_due) and (eff_due < today)
            overdue_days  = (today - eff_due).days if overdue_flag else 0

            all_invoices.append({
                "inv_no":       inv_no,
                "client":       client,
                "contract":     cno,
                "sum_uah":      sum_uah,
                "inv_date":     inv_date,
                "due":          eff_due,
                "status":       status,
                "is_overdue":   overdue_flag,
                "overdue_days": overdue_days,
                "inv_date_str": fmt_date(inv_date),
                "due_date_str": fmt_date(eff_due),
            })

            # ROI: Access revenue all time
            if status == "Оплачено":
                ct = cno_to_ctype.get(cno, "")
                if ct == "Доступ" or inv_type in ("Доступ", "Access"):
                    access_revenue_all_time += sum_uah

            if status != "Оплачено" and eff_due:
                if eff_due < today:
                    debt_real += sum_uah
                else:
                    debt_pending += sum_uah

        all_invoices.sort(key=lambda i: (
            0 if i["is_overdue"] else (1 if i["status"] != "Оплачено" else 2),
            -i["overdue_days"]
        ))

        # ── MRR / ARR (active Доступ contracts, normalized to /month) ──
        mrr = 0.0
        for c in contracts_list:
            if c["status"] != "Активний" or c["ctype"] != "Доступ" or not c["ok"]:
                continue
            info = last_inv_period.get(c["no"])
            if not info:
                continue
            pf = info["from"]
            pt = info["to"]
            sv = info.get("sum_uah", 0)
            if pf and pt:
                mc = (pt.year * 12 + pt.month) - (pf.year * 12 + pf.month) + 1
                mc = max(1, mc)
            else:
                mc = 3  # default quarterly
            mrr += sv / mc

        # ── Acts ──────────────────────────────────────────────
        rows_a = db.execute("SELECT * FROM acts").fetchall()

        acts_unsigned      = 0
        sent_unsigned_acts = []
        acts_exist         = set()

        for r in rows_a:
            cno        = r["contract_no"] or ""
            client     = r["client_name"] or ""
            act_status = norm(r["status"]) or ""
            act_date   = parse_date(r["act_date"])
            act_no     = r["act_no"] or ""
            sum_uah    = float(r["sum_uah"] or 0)
            pto        = parse_date(r["period_to"])

            if cno in cno_to_ctype:
                if cno not in ok_cnos:
                    continue
            else:
                if not (ALL_C or (client_filter and client_filter in client)):
                    continue

            # Cancelled acts still "own" their invoice slot
            if pto and act_status and act_status.lower() not in ("видалено", "deleted", ""):
                acts_exist.add((cno, pto))

            if act_status not in ("Підписано", "Скасовано"):
                acts_unsigned += 1
                if act_date:
                    sent_unsigned_acts.append({
                        "act_no":       act_no,
                        "client":       client,
                        "contract":     cno,
                        "act_date":     act_date,
                        "sum_uah":      sum_uah,
                        "act_status":   act_status,
                        "overdue_days": (today - act_date).days,
                    })

        sent_unsigned_acts.sort(key=lambda x: x["overdue_days"], reverse=True)

        # ── Next invoices (Access contracts) ──────────────────
        # Returns the last day of the month that is (months-1) months after start.
        # Example: start=2026-07-01, months=3 → September = end of Q3 → 2026-09-30
        def _add_months_end(start: date, months: int) -> date:
            total = start.year * 12 + (start.month - 1) + (months - 1)
            y, m = divmod(total, 12)
            m += 1
            last = calendar.monthrange(y, m)[1]
            return date(y, m, last)

        DAYS_BEFORE = 5  # issue invoice N days before next period starts

        next_invoices = []
        for c in contracts_list:
            if c["status"] != "Активний" or not c["ok"]:
                continue
            if c["ctype"] != "Доступ":
                continue

            info = last_inv_period.get(c["no"])
            if not info:
                continue

            pfrom = info["from"]
            pto   = info["to"]

            # Next period starts the day after last period ends
            next_start = pto + timedelta(days=1)

            # Infer period length in months from the last invoice
            if pfrom:
                months_count = (
                    (pto.year * 12 + pto.month) - (pfrom.year * 12 + pfrom.month) + 1
                )
                months_count = max(1, months_count)
            else:
                months_count = 3  # default: quarterly

            # Suggested next period end (same duration)
            next_end = _add_months_end(next_start, months_count)

            # Deadline to issue = DAYS_BEFORE days before next period starts
            issue_by  = next_start - timedelta(days=DAYS_BEFORE)
            days_left = (issue_by - today).days

            # Period label for display
            period_label = (
                f"{next_start.strftime('%d.%m')} – {next_end.strftime('%d.%m.%Y')}"
            )

            next_invoices.append({
                "contract":     c["no"],
                "client":       c["client"],
                "next_start":   next_start,
                "next_end":     next_end,
                "issue_by":     issue_by,
                "period_label": period_label,
                "months_count": months_count,
                "days_left":    days_left,
                "tariff":       f"{c['tariff']} {c['curr']}",
            })

        next_invoices.sort(key=lambda x: x["days_left"])
        for ni in next_invoices:
            dl = ni["days_left"]
            ni["urgency"] = ("overdue" if dl < 0 else
                             "urgent"  if dl <= 3 else
                             "soon"    if dl <= 14 else "ok")

        # ── Invoices without acts ─────────────────────────────
        seen_inv_acts = set()
        pending_acts  = []
        for r in rows_i:
            cno_i   = r["contract_no"] or ""
            client_i= r["client_name"] or ""
            inv_no  = r["invoice_no"]  or ""
            pto_i   = parse_date(r["period_to"])
            pfrom_i = parse_date(r["period_from"])
            sum_i   = float(r["sum_uah"] or 0)
            status_i= norm(r["pay_status"])

            if status_i == "Скасовано":
                continue
            if cno_i in cno_to_ctype:
                if cno_i not in ok_cnos:
                    continue
                if cno_to_ctype[cno_i] != "Доступ":
                    continue
            else:
                continue

            if not pto_i:
                continue
            key = (cno_i, pto_i)
            if key in seen_inv_acts:
                continue
            seen_inv_acts.add(key)
            if key in acts_exist:
                continue

            dl = (pto_i - today).days
            pending_acts.append({
                "inv_no":      inv_no,
                "contract":    cno_i,
                "client":      client_i,
                "period_from": pfrom_i,
                "period_to":   pto_i,
                "sum_uah":     sum_i,
                "days_left":   dl,
                "overdue":     dl < 0,
            })

        overdue_acts  = sorted([a for a in pending_acts if a["overdue"]],
                               key=lambda x: x["days_left"])
        upcoming_acts = sorted([a for a in pending_acts if not a["overdue"]],
                               key=lambda x: x["days_left"])
        next_acts = overdue_acts + upcoming_acts
        for na in next_acts:
            dl = na["days_left"]
            na["urgency"] = ("overdue" if dl < 0 else
                             "urgent"  if dl <= 3 else
                             "soon"    if dl <= 10 else "ok")

        # ── Expiring contracts ────────────────────────────────
        expiring_contracts = []
        for r in rows_c:
            if norm(r["status"]) != "Активний":
                continue
            end_d = parse_date(r["contract_end"])
            if not end_d:
                continue
            days_left = (end_d - today).days
            if 0 <= days_left <= 60:
                expiring_contracts.append({
                    "contract_no":  r["contract_no"],
                    "client_name":  r["client_name"],
                    "contract_end": r["contract_end"],
                    "days_left":    days_left,
                })
        expiring_contracts.sort(key=lambda x: x["days_left"])

        # ── Expenses ──────────────────────────────────────────
        rows_e = db.execute("SELECT * FROM expenses").fetchall()

        expenses_period   = 0.0
        expenses_all_time = 0.0
        expenses_by_cat   = {}
        monthly_expenses  = {}  # {(year, month): {category: total}}

        for r in rows_e:
            exp_date   = parse_date(r["exp_date"])
            category   = r["category"] or ""
            amount_uah = float(r["amount_uah"] or r["amount"] or 0)

            expenses_all_time += amount_uah

            if exp_date and date_from <= exp_date <= date_to:
                expenses_period += amount_uah
                expenses_by_cat[category] = expenses_by_cat.get(category, 0) + amount_uah

            if exp_date and exp_date.year == today.year:
                key = (exp_date.year, exp_date.month)
                monthly_expenses.setdefault(key, {})
                monthly_expenses[key][category] = (
                    monthly_expenses[key].get(category, 0) + amount_uah)

        # ── AppPayments ───────────────────────────────────────
        rows_ap = db.execute("SELECT * FROM app_payments").fetchall()

        app_revenue_period   = 0.0
        app_revenue_all_time = 0.0
        app_payments_list    = []

        for r in rows_ap:
            ap_date = parse_date(r["pay_date"])
            ap_amt  = float(r["amount"] or 0)
            app_revenue_all_time += ap_amt
            if ap_date and date_from <= ap_date <= date_to:
                app_revenue_period += ap_amt
                app_payments_list.append({
                    "pay_date":    ap_date,
                    "amount":      ap_amt,
                    "description": r["description"] or "",
                    "source":      r["source"] or "",
                })

        # ── Trend: last 6 months income + expenses ─────────────
        _MON_ABBR = ['Січ','Лют','Бер','Кві','Тра','Чер',
                     'Лип','Сер','Вер','Жов','Лис','Гру']
        monthly_income = {}
        for r in rows_i:
            # Trend: only Доступ contracts / invoice types
            cno_r      = r["contract_no"] or ""
            inv_type_r = norm(r["invoice_type"] or "")
            if (cno_to_ctype.get(cno_r, "") != "Доступ"
                    and inv_type_r not in ("Доступ", "Access")):
                continue
            pay_d = parse_date(r["pay_date"])
            if pay_d and norm(r["pay_status"]) == "Оплачено":
                key = (pay_d.year, pay_d.month)
                monthly_income[key] = (monthly_income.get(key, 0)
                                       + float(r["sum_uah"] or 0))

        trend = []
        for i in range(5, -1, -1):
            m = today.month - i
            y = today.year
            while m <= 0:
                m += 12
                y -= 1
            key = (y, m)
            trend.append({
                "label":    _MON_ABBR[m - 1],
                "income":   round(monthly_income.get(key, 0)),
                "expenses": round(sum(monthly_expenses.get(key, {}).values())),
            })

        # ── Activity feed (recent 12 events) ───────────────────
        _MON_GEN = ['січ','лют','бер','кві','тра','чер',
                    'лип','сер','вер','жов','лис','гру']
        activity = []

        for r in rows_i:
            pay_d = parse_date(r["pay_date"])
            if (pay_d and norm(r["pay_status"]) == "Оплачено"
                    and (today - pay_d).days <= 30):
                activity.append({
                    "type": "payment",
                    "date": pay_d,
                    "text": (f"Оплата ₴{fmt_money(float(r['sum_uah'] or 0))} "
                             f"від {r['client_name']} ({r['invoice_no']})"),
                })

        for r in rows_a:
            act_d  = parse_date(r["act_date"])
            act_st = norm(r["status"]) or ""
            if not act_d:
                continue
            days_ago = (today - act_d).days
            if act_st == "Підписано" and days_ago <= 30:
                activity.append({
                    "type": "act_signed",
                    "date": act_d,
                    "text": f"Підписано акт {r['act_no']} — {r['client_name']}",
                })
            elif act_st in ("Надіслано",) and days_ago <= 14:
                activity.append({
                    "type": "act_sent",
                    "date": act_d,
                    "text": f"Надіслано акт {r['act_no']} — {r['client_name']}",
                })
            elif act_st in ("Draft", "Чернетка", "") and days_ago <= 14:
                activity.append({
                    "type": "act_created",
                    "date": act_d,
                    "text": f"Створено акт {r['act_no']} — {r['client_name']}",
                })

        for r in rows_i:
            inv_d = parse_date(r["invoice_date"])
            if inv_d and (today - inv_d).days <= 14:
                activity.append({
                    "type": "invoice",
                    "date": inv_d,
                    "text": (f"Виставлено {r['invoice_no']} "
                             f"на ₴{fmt_money(float(r['sum_uah'] or 0))} "
                             f"— {r['client_name']}"),
                })

        for r in rows_e:
            exp_d = parse_date(r["exp_date"])
            if exp_d and (today - exp_d).days <= 14:
                activity.append({
                    "type": "expense",
                    "date": exp_d,
                    "text": (f"Витрата ₴{fmt_money(float(r['amount_uah'] or r['amount'] or 0))} "
                             f"— {r['category']}"),
                })

        for r in rows_ap:
            pay_d2 = parse_date(r["pay_date"])
            if pay_d2 and (today - pay_d2).days <= 14:
                activity.append({
                    "type": "lyqpay",
                    "date": pay_d2,
                    "text": (f"LiqPay ₴{fmt_money(float(r['amount'] or 0))} "
                             f"— {r['description'] or 'без опису'}"),
                })

        activity.sort(key=lambda x: x["date"], reverse=True)
        activity = activity[:5]

        for a in activity:
            d = a["date"]
            if d == today:
                a["when"] = "сьогодні"
            elif (today - d).days == 1:
                a["when"] = "вчора"
            else:
                a["when"] = f"{d.day} {_MON_GEN[d.month - 1]}"

    # ── ROI ───────────────────────────────────────────────────
    roi = 0.0
    if expenses_all_time:
        roi = (access_revenue_all_time + app_revenue_all_time - expenses_all_time) \
              / expenses_all_time * 100

    # ── Monthly revenue for chart (current year) ──────────────
    months_chart = []
    for m in range(1, 13):
        key = (today.year, m)
        months_chart.append({
            "month": m,
            "label": f"{m:02d}.{today.year}",
        })

    return {
        "today":                   today,
        "date_from":               date_from,
        "date_to":                 date_to,
        "client_filter":           client_filter,
        "ctype_filter":            ctype_filter,
        "clients_active":          clients_active,
        "contracts_active":        contracts_active,
        "total_users":             total_users,
        "acts_unsigned":           acts_unsigned,
        "inv_period":              inv_period,
        "paid_period":             paid_period,
        "paid_invoices":           paid_invoices,
        "debt_real":               debt_real,
        "debt_pending":            debt_pending,
        "all_invoices":            all_invoices,
        "sent_unsigned_acts":      sent_unsigned_acts,
        "next_invoices":           next_invoices,
        "next_acts":               next_acts,
        "overdue_acts_count":      len(overdue_acts),
        "client_names":            all_client_names,
        "expenses_period":         expenses_period,
        "expenses_all_time":       expenses_all_time,
        "expenses_by_cat":         expenses_by_cat,
        "app_revenue_period":      app_revenue_period,
        "app_revenue_all_time":    app_revenue_all_time,
        "app_payments_list":       app_payments_list,
        "access_revenue_all_time": access_revenue_all_time,
        "roi":                     roi,
        "mrr":                     round(mrr, 2),
        "arr":                     round(mrr * 12, 2),
        "months_chart":            months_chart,
        "expiring_contracts":      expiring_contracts,
        "trend":                   trend,
        "activity":                activity,
    }


# ── Routes ────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request:   Request,
    date_from: str = Query(default=""),
    date_to:   str = Query(default=""),
    period:    str = Query(default=""),
    client:    str = Query(default=""),
    ctype:     str = Query(default=""),
):
    today = date.today()
    active_period = period

    if period == "week":
        df = today - timedelta(days=today.weekday())   # Monday
        dt = today
    elif period == "quarter":
        q = ((today.month - 1) // 3) * 3 + 1
        df = date(today.year, q, 1)
        dt = today
    elif period == "year":
        df = date(today.year, 1, 1)
        dt = today
    elif period == "month":
        df = _month_start(today)
        dt = _month_end(today)
    else:
        # Custom range or first load (default → current month)
        df = parse_date(date_from) or _month_start(today)
        dt = parse_date(date_to)   or _month_end(today)
        if not date_from:
            active_period = "month"

    data = compute_kpi(df, dt, client, ctype)

    # ── NBU rate alerts ────────────────────────────────────────────
    with get_db() as db:
        active_contracts = db.execute(
            "SELECT contract_no, client_name, nbu_rate, nbu_tracking, nbu_threshold_pct "
            "FROM contracts WHERE status='Активний'"
        ).fetchall()
    nbu = nbu_client.compute_alerts(active_contracts)
    data["nbu_alerts"]       = nbu["alerts"]
    data["nbu_current_rate"] = nbu["current_rate"]
    data["nbu_rate_date"]    = nbu["rate_date"]
    data["nbu_error"]        = nbu["error"]

    data["request"]       = request
    data["active_period"] = active_period
    return templates.TemplateResponse("dashboard.html", data)


@router.post("/invoices/{invoice_no}/cancel", response_class=HTMLResponse)
async def cancel_invoice(invoice_no: str, request: Request):
    with get_db() as db:
        db.execute(
            "UPDATE invoices SET pay_status='Скасовано' WHERE invoice_no=?",
            (invoice_no,)
        )
    return HTMLResponse(
        '<tr class="hidden" id="inv-row-cancelled"></tr>',
        headers={"HX-Trigger": "dashboardRefresh"}
    )


@router.post("/acts/{act_no}/cancel", response_class=HTMLResponse)
async def cancel_act(act_no: str, request: Request):
    with get_db() as db:
        db.execute(
            "UPDATE acts SET status='Скасовано' WHERE act_no=?",
            (act_no,)
        )
    return HTMLResponse(
        '<tr class="hidden"></tr>',
        headers={"HX-Trigger": "dashboardRefresh"}
    )


@router.get("/api/nbu-rate")
async def api_nbu_rate():
    """Returns current NBU USD/UAH rate (cached or live)."""
    try:
        rate, rate_date, from_cache = nbu_client.get_rate()
        return JSONResponse({
            "rate":       rate,
            "date":       rate_date,
            "from_cache": from_cache,
        })
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
