"""LiqPay API client — fetches payment register directly from LiqPay."""

import os
import hashlib
import base64
import json
import requests
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Union


def get_keys() -> tuple:
    return os.getenv("LIQPAY_PUBLIC_KEY", ""), os.getenv("LIQPAY_PRIVATE_KEY", "")


def configured() -> bool:
    pub, priv = get_keys()
    return bool(pub and priv)


def _b64(data: dict) -> str:
    return base64.b64encode(json.dumps(data, ensure_ascii=False).encode("utf-8")).decode()


def _sign(private_key: str, data_b64: str) -> str:
    raw = (private_key + data_b64 + private_key).encode("utf-8")
    return base64.b64encode(hashlib.sha1(raw).digest()).decode()


def _ts_ms(d: date, end: bool = False) -> int:
    t = datetime(d.year, d.month, d.day, 23, 59, 59) if end else datetime(d.year, d.month, d.day, 0, 0, 0)
    return int(t.timestamp() * 1000)


def _api_call(params: dict) -> Union[dict, list]:
    pub, priv = get_keys()
    params["public_key"] = pub
    data_b64  = _b64(params)
    signature = _sign(priv, data_b64)
    resp = requests.post(
        "https://www.liqpay.ua/api/request",
        data={"data": data_b64, "signature": signature},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_payments(date_from: date, date_to: date) -> tuple:
    """
    Fetch payment register from LiqPay for the given date range.
    Splits automatically into ≤89-day chunks.
    Returns (payments: list, debug: list[str]).
    """
    pub, priv = get_keys()
    if not pub or not priv:
        raise RuntimeError("LiqPay ключі не налаштовані.")

    all_payments = []
    debug = []

    # Split range into max 89-day chunks
    chunk_start = date_from
    while chunk_start <= date_to:
        chunk_end = min(chunk_start + timedelta(days=88), date_to)

        params = {
            "action":      "reports",
            "version":     3,
            "date_from":   _ts_ms(chunk_start),
            "date_to":     _ts_ms(chunk_end, end=True),
            "resp_format": "json",
        }
        try:
            body = _api_call(params)
            snippet = json.dumps(body, ensure_ascii=False)
            debug.append(f"{chunk_start}..{chunk_end}: {snippet[:400]}")

            rows = []
            if isinstance(body, dict):
                if body.get("result") == "error" or body.get("status") == "error":
                    err = body.get("err_description") or body.get("err_code") or "error"
                    debug.append(f"  → error: {err}")
                else:
                    data = body.get("data", [])
                    if isinstance(data, list):
                        rows = data
                    elif isinstance(data, dict) and data.get("result") == "error":
                        debug.append(f"  → inner error: {data.get('err_description')}")
            elif isinstance(body, list):
                rows = body

            all_payments.extend(_normalize(r) for r in rows if isinstance(r, dict))

        except Exception as e:
            debug.append(f"{chunk_start}..{chunk_end}: exception — {e}")

        chunk_start = chunk_end + timedelta(days=1)

    return all_payments, debug


PAYTYPE_LABELS = {
    "apay":      "Apple Pay",
    "gpay":      "Google Pay",
    "privat24":  "Privat24",
    "monobank":  "Monobank",
    "card":      "Картка",
    "liqpay":    "LiqPay",
    "qr":        "QR-код",
    "cash":      "Готівка",
}


def _normalize(r: dict) -> dict:
    ts = r.get("end_date") or r.get("create_date")
    try:
        pay_date = datetime.fromtimestamp(int(ts) / 1000).date() if ts else None
    except Exception:
        pay_date = None

    first = (r.get("sender_first_name") or "").strip()
    last  = (r.get("sender_last_name")  or "").strip()
    full_name = f"{first} {last}".strip()

    paytype = r.get("paytype") or ""
    paytype_label = PAYTYPE_LABELS.get(paytype, paytype)

    return {
        "payment_id":   str(r.get("payment_id") or r.get("transaction_id") or ""),
        "date":         pay_date,
        "date_str":     pay_date.strftime("%d.%m.%Y") if pay_date else "",
        "amount":       float(r.get("amount") or 0),
        "commission":   float(r.get("commission_credit") or r.get("receiver_commission") or 0),
        "currency":     r.get("currency") or "UAH",
        "status":       r.get("status") or "",
        "description":  r.get("description") or "",
        "order_id":     r.get("order_id") or "",
        "payer_name":   full_name,
        "payer_phone":  r.get("sender_phone") or "",
        "payer_card":   r.get("sender_card_mask2") or "",
        "payer_bank":   r.get("sender_card_bank") or "",
        "paytype":      paytype,
        "paytype_label": paytype_label,
        "action":       r.get("action") or "",
    }


STATUS_LABELS = {
    "success":     ("Успішно",   "pill-success"),
    "failure":     ("Помилка",   "pill-danger"),
    "error":       ("Помилка",   "pill-danger"),
    "wait_accept": ("Очікує",    "pill-warning"),
    "processing":  ("В процесі", "pill-warning"),
    "reversed":    ("Повернуто", "pill-mute"),
    "refunded":    ("Повернуто", "pill-mute"),
}


def status_label(status: str) -> tuple:
    return STATUS_LABELS.get(status, (status or "—", "pill-mute"))
