"""
utils.py
Допоміжні функції: сума прописом, розрахунки, форматування
"""

from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from num2words import num2words
from config import DATE_FORMAT


# ============================================================
# СУМА ПРОПИСОМ
# ============================================================

def amount_to_words_uah(amount: float) -> str:
    """
    Перетворює суму в гривнях на рядок прописом.
    Наприклад: 18684 → 'вісімнадцять тисяч шістсот вісімдесят чотири гривні 00 коп.'
    """
    amount = round(float(amount), 2)
    hryvnias = int(amount)
    kopecks = round((amount - hryvnias) * 100)

    hryvnia_words = num2words(hryvnias, lang='uk')

    last_two = hryvnias % 100
    last_one = hryvnias % 10

    if 11 <= last_two <= 19:
        hryvnia_form = "гривень"
    elif last_one == 1:
        hryvnia_form = "гривня"
    elif 2 <= last_one <= 4:
        hryvnia_form = "гривні"
    else:
        hryvnia_form = "гривень"

    return f"{hryvnia_words} {hryvnia_form} {kopecks:02d} коп."


# ============================================================
# ФОРМАТУВАННЯ ЧИСЕЛл
# ============================================================

def fmt_money(amount: float) -> str:
    """18684 → '18 684,00'"""
    return f"{amount:,.2f}".replace(",", " ").replace(".", ",")


def fmt_date(val) -> str:
    """datetime/date → 'dd.mm.yyyy'"""
    if val is None:
        return ""
    if isinstance(val, (datetime, date)):
        return val.strftime(DATE_FORMAT)
    return str(val)


# ============================================================
# РОЗРАХУНОК МІСЯЦІВ
# ============================================================

def count_months(date_from: date, date_to: date) -> int:
    """
    Рахує кількість повних місяців між двома датами.
    Якщо не ціле — округлює вгору.
    Наприклад: 01.01.2026–31.03.2026 = 3 місяці
    """
    if not date_from or not date_to:
        return 0

    rd = relativedelta(date_to, date_from)
    months = rd.years * 12 + rd.months

    # Якщо є залишкові дні — додаємо 1 місяць (округлення вгору)
    if rd.days > 0:
        months += 1

    return max(months, 1)


# ============================================================
# РОЗРАХУНОК СУМИ ДЛЯ ACCESS ДОГОВОРУ
# ============================================================

def calc_access_sum(users: int, months: int, tariff_uah: float) -> float:
    """Сума = кількість користувачів × місяців × тариф"""
    return round(users * months * tariff_uah, 2)


def calc_access_tariff_uah(tariff_fx: float, currency: str, fx_rate: float) -> float:
    """
    Конвертує тариф у валюті в гривні.
    Якщо валюта UAH — повертає тариф без змін.
    """
    if currency == "UAH":
        return round(float(tariff_fx), 2)
    return round(float(tariff_fx) * float(fx_rate), 2)


# ============================================================
# РОЗРАХУНОК СУМИ ДЛЯ HOURLY ДОГОВОРУ
# ============================================================

def calc_hourly_sum(hours: float, hour_rate: float) -> float:
    """Сума = години × ставка"""
    return round(hours * hour_rate, 2)


# ============================================================
# DUE DATE
# ============================================================

def calc_due_date_access(invoice_date: date) -> date:
    """Для Access: Сплатити до = дата рахунку + 5 днів"""
    from datetime import timedelta
    return invoice_date + timedelta(days=5)


def calc_due_date_hourly() -> date:
    """Для Hourly: Сплатити до = сьогодні + 5 днів"""
    from datetime import timedelta
    return date.today() + timedelta(days=5)
