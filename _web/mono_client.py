"""
mono_client.py — Monobank Open API integration + payment matching logic
for the DocketPro CRM web version.

Adapted from _app/privat_api.py and _app/payment_matcher_app.py
"""

import os
import re
import requests
from datetime import date, datetime, timedelta
from typing import Optional
from dotenv import load_dotenv

# .env is loaded by main.py before this module is imported.
# Fallback: try to load it here too in case of direct import.
from pathlib import Path as _Path
_env_path = str(_Path(__file__).resolve().parent.parent / ".env")
load_dotenv(_env_path, override=False)  # don't override if already set by main.py

def get_token() -> str:
    return os.getenv("MONO_TOKEN", "")

def get_iban() -> str:
    return os.getenv("MONO_IBAN", "")

# Keep these for backwards compat — will be updated at first access
MONO_TOKEN = os.getenv("MONO_TOKEN", "")
MONO_IBAN  = os.getenv("MONO_IBAN",  "")

MONO_BASE = "https://api.monobank.ua"
_CURRENCY = {980: "UAH", 840: "USD", 978: "EUR", 826: "GBP"}

INVOICE_RE = re.compile(r"(RAH-\d{8}-\d{3})", re.IGNORECASE)

# Module-level cache — persists across HTTP requests within one uvicorn process
_cached_account_id: Optional[str] = None

# Abbreviations for long company names
_NAME_SHORTS = [
    ("ТОВАРИСТВО З ОБМЕЖЕНОЮ ВІДПОВІДАЛЬНІСТЮ", "ТОВ"),
    ("ПРИВАТНЕ АКЦІОНЕРНЕ ТОВАРИСТВО",          "ПрАТ"),
    ("ПУБЛІЧНЕ АКЦІОНЕРНЕ ТОВАРИСТВО",          "ПАТ"),
    ("АКЦІОНЕРНЕ ТОВАРИСТВО",                   "АТ"),
    ("ДЕРЖАВНЕ ПІДПРИЄМСТВО",                   "ДП"),
    ("ФІЗИЧНА ОСОБА ПІДПРИЄМЕЦЬ",               "ФОП"),
    ("ФІЗИЧНА ОСОБА-ПІДПРИЄМЕЦЬ",               "ФОП"),
    ("АДВОКАТСЬКЕ ОБʼЄДНАННЯ",                  "АО"),
]


def shorten_name(name: str) -> str:
    result = name or ""
    for full, short in _NAME_SHORTS:
        result = result.replace(full, short)
    return result.strip().strip('"').strip("«»").strip()


# ── MonoClient ────────────────────────────────────────────────

class MonoClient:

    def __init__(self, token: str, iban: str = ""):
        self.token = token
        self.iban  = iban.replace(" ", "")
        self._account_id: Optional[str] = None

    def _headers(self) -> dict:
        return {"X-Token": self.token, "User-Agent": "DocketPro/1.0"}

    def _request(self, endpoint: str):
        url  = MONO_BASE + endpoint
        resp = requests.get(url, headers=self._headers(), timeout=30)
        if resp.status_code == 429:
            raise RuntimeError(
                "Monobank API: занадто багато запитів (429). "
                "Зачекайте 60 секунд і спробуйте ще раз."
            )
        resp.raise_for_status()
        return resp.json()

    def _get_account_id(self) -> str:
        global _cached_account_id
        # Return module-level cache first (survives across requests)
        if _cached_account_id:
            self._account_id = _cached_account_id
            return _cached_account_id

        data     = self._request("/personal/client-info")
        accounts = data.get("accounts", [])

        if self.iban:
            for acc in accounts:
                if acc.get("iban", "").replace(" ", "") == self.iban:
                    self._account_id = acc["id"]
                    _cached_account_id = acc["id"]
                    return self._account_id

        for acc in accounts:
            if acc.get("currencyCode") == 980:
                self._account_id = acc["id"]
                _cached_account_id = acc["id"]
                return self._account_id

        if accounts:
            self._account_id = accounts[0]["id"]
            _cached_account_id = accounts[0]["id"]
            return self._account_id

        return "0"

    def get_transactions(self, date_from: date, date_to: date) -> list:
        """Monobank limits one request to 31 days; chunks automatically."""
        account_id  = self._get_account_id()
        all_txns    = []
        chunk_start = date_from

        while chunk_start <= date_to:
            chunk_end = min(chunk_start + timedelta(days=30), date_to)
            ts_from   = int(datetime.combine(chunk_start, datetime.min.time()).timestamp())
            ts_to     = int(datetime.combine(chunk_end,   datetime.max.time()).timestamp())

            data = self._request(f"/personal/statement/{account_id}/{ts_from}/{ts_to}")
            if isinstance(data, list):
                all_txns.extend(data)

            chunk_start = chunk_end + timedelta(days=1)

        return all_txns


# ── Parsing ───────────────────────────────────────────────────

def parse_transactions(raw_list: list) -> list:
    """Normalize raw Monobank transactions; keep only incoming (amount > 0)."""
    result = []
    for t in raw_list:
        raw_amount = t.get("amount", 0)
        if raw_amount <= 0:
            continue

        amount = raw_amount / 100
        ts = t.get("time")
        try:
            pay_date = datetime.fromtimestamp(ts).date()
        except (TypeError, ValueError, OSError):
            pay_date = None

        currency = _CURRENCY.get(t.get("currencyCode", 980), "UAH")

        result.append({
            "pay_date":    pay_date,
            "amount":      amount,
            "currency":    currency,
            "purpose":     str(t.get("description")  or ""),
            "payer":       str(t.get("counterName")   or ""),
            "edrpou":      str(t.get("counterEdrpou") or "").strip(),
            "invoice_nos": [],
        })
    return result


def enrich_with_invoice_nos(payments: list) -> list:
    for pay in payments:
        nos = INVOICE_RE.findall(pay.get("purpose", ""))
        pay["invoice_nos"] = [n.upper() for n in nos]
    return payments


# ── Levenshtein ───────────────────────────────────────────────

def _levenshtein(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
        prev = curr
    return prev[-1]


# ── Matching ──────────────────────────────────────────────────

def match_payments(payments: list, invoices: list) -> list:
    """
    3-level matching:
      1. exact  — RAH-DDMMYYYY-NNN in purpose matches invoice_no exactly
      2. fuzzy  — Levenshtein distance ≤ 3 to any unpaid invoice_no
      3. edrpou — same EDRPOU + amount within ±5% (only if no RAH- ref found)
    """
    inv_by_no     = {inv["invoice_no"].upper(): inv for inv in invoices}
    inv_by_edrpou: dict[str, list] = {}
    for inv in invoices:
        ed = str(inv.get("edrpou") or "").strip()
        if ed:
            inv_by_edrpou.setdefault(ed, []).append(inv)

    results = []
    for pay in payments:
        matched: list[dict] = []

        # 1. Exact
        for inv_no in pay.get("invoice_nos", []):
            inv = inv_by_no.get(inv_no)
            if inv:
                matched.append({"invoice": inv, "match_type": "exact"})

        has_rah = len(pay.get("invoice_nos", [])) > 0

        # 2. Fuzzy
        if not matched and has_rah:
            for ref_no in pay.get("invoice_nos", []):
                best_inv  = None
                best_dist = 999
                for inv_no, inv in inv_by_no.items():
                    dist = _levenshtein(ref_no, inv_no)
                    if dist < best_dist:
                        best_dist = dist
                        best_inv  = inv
                if best_inv and best_dist <= 3:
                    matched.append({
                        "invoice": best_inv,
                        "match_type": "fuzzy",
                        "edit_distance": best_dist,
                    })

        # 3. EDRPOU + amount ±5%
        if not matched and not has_rah and pay.get("edrpou"):
            pay_sum = float(pay.get("amount") or 0)
            for inv in inv_by_edrpou.get(pay["edrpou"], []):
                inv_sum = float(inv.get("sum_uah") or 0)
                if inv_sum > 0 and abs(pay_sum - inv_sum) / inv_sum <= 0.05:
                    matched.append({"invoice": inv, "match_type": "edrpou"})

        results.append({"payment": pay, "matches": matched})

    return results
