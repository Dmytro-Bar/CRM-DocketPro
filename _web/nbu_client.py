"""NBU (National Bank of Ukraine) USD/UAH rate client with DB cache."""

import time
import requests
from database import get_db

NBU_API_URL    = "https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange"
CACHE_MAX_AGE  = 6 * 3600   # 6 hours in seconds
REQUEST_TIMEOUT = 6          # seconds


# ── Raw API fetch ──────────────────────────────────────────────────────────

def fetch_from_api() -> tuple:
    """Fetch current USD/UAH rate directly from NBU API.
    Returns (rate: float, rate_date: str) where rate_date is 'дд.мм.рррр'.
    Raises on any error.
    """
    resp = requests.get(
        NBU_API_URL,
        params={"valcode": "USD", "json": ""},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError("НБУ API повернув порожній список")
    item = data[0]
    return float(item["rate"]), item["exchangedate"]   # 'дд.мм.рррр'


# ── Cached rate ────────────────────────────────────────────────────────────

def get_rate() -> tuple:
    """Returns (rate: float, rate_date: str, from_cache: bool).
    Reads from nbu_rate_cache; fetches from API if cache is stale (>6 h).
    Raises if API unavailable and cache empty.
    """
    now_ts = int(time.time())

    with get_db() as db:
        row = db.execute(
            "SELECT rate, rate_date, fetched_ts "
            "FROM nbu_rate_cache ORDER BY id DESC LIMIT 1"
        ).fetchone()

    if row and (now_ts - int(row["fetched_ts"])) < CACHE_MAX_AGE:
        return float(row["rate"]), row["rate_date"], True

    # Cache miss — fetch from API
    rate, rate_date = fetch_from_api()
    with get_db() as db:
        db.execute(
            "INSERT INTO nbu_rate_cache (fetched_ts, rate, rate_date) VALUES (?,?,?)",
            (now_ts, rate, rate_date)
        )
    return rate, rate_date, False


def get_rate_safe() -> tuple:
    """Like get_rate() but never raises — returns (rate, rate_date, from_cache, error).
    error is None on success, or a string describing the problem.
    """
    try:
        rate, rate_date, from_cache = get_rate()
        return rate, rate_date, from_cache, None
    except Exception as exc:
        return None, None, False, str(exc)


# ── Full status (all tracked contracts) ───────────────────────────────────

def compute_status(active_contracts) -> dict:
    """
    Returns status for ALL NBU-tracked contracts (not just alerts).
    Used by dashboard to show the full NBU table + current rate.

    Returns dict:
      {
        "tracked":      list of status dicts (all tracked contracts),
        "alerts":       list of status dicts (only those >= threshold),
        "current_rate": float | None,
        "rate_date":    str   | None,
        "error":        str   | None,
      }

    Each status dict:
      contract_no, client, base_rate, current_rate, delta_pct, threshold, is_alert
    """
    tracked_rows = [
        r for r in active_contracts
        if r["nbu_tracking"] and float(r["nbu_rate"] or 0) > 0
    ]

    if not tracked_rows:
        return {"tracked": [], "alerts": [], "current_rate": None,
                "rate_date": None, "error": None}

    rate, rate_date, _, error = get_rate_safe()
    if error:
        return {"tracked": [], "alerts": [], "current_rate": None,
                "rate_date": None, "error": error}

    tracked = []
    alerts  = []
    for r in tracked_rows:
        base_rate = float(r["nbu_rate"])
        threshold = float(r["nbu_threshold_pct"] or 5.0)
        delta_pct = (rate - base_rate) / base_rate * 100
        is_alert  = delta_pct >= threshold
        item = {
            "contract_no":  r["contract_no"],
            "client":       r["client_name"] or "",
            "base_rate":    round(base_rate, 2),
            "current_rate": round(rate, 2),
            "delta_pct":    round(delta_pct, 2),
            "threshold":    threshold,
            "is_alert":     is_alert,
        }
        tracked.append(item)
        if is_alert:
            alerts.append(item)

    tracked.sort(key=lambda a: a["delta_pct"], reverse=True)
    alerts.sort(key=lambda a: a["delta_pct"], reverse=True)
    return {
        "tracked":      tracked,
        "alerts":       alerts,
        "current_rate": round(rate, 2),
        "rate_date":    rate_date,
        "error":        None,
    }


# ── Alert computation (legacy — kept for contracts list page) ──────────────

def compute_alerts(active_contracts) -> dict:
    """
    Given a list of active contract rows (sqlite3.Row or dict-like),
    returns a dict:
      {
        "alerts":       list of alert dicts,
        "current_rate": float | None,
        "rate_date":    str   | None,
        "error":        str   | None,
      }

    Each alert dict:
      contract_no, client, base_rate, current_rate, delta_pct, threshold
    """
    tracked = [
        r for r in active_contracts
        if r["nbu_tracking"] and float(r["nbu_rate"] or 0) > 0
    ]

    if not tracked:
        return {"alerts": [], "current_rate": None, "rate_date": None, "error": None}

    rate, rate_date, _, error = get_rate_safe()
    if error:
        return {"alerts": [], "current_rate": None, "rate_date": None, "error": error}

    alerts = []
    for r in tracked:
        base_rate = float(r["nbu_rate"])
        threshold = float(r["nbu_threshold_pct"] or 5.0)
        delta_pct = (rate - base_rate) / base_rate * 100
        if delta_pct >= threshold:
            alerts.append({
                "contract_no":  r["contract_no"],
                "client":       r["client_name"] or "",
                "base_rate":    round(base_rate, 2),
                "current_rate": round(rate, 2),
                "delta_pct":    round(delta_pct, 1),
                "threshold":    threshold,
            })

    # Sort: biggest delta first
    alerts.sort(key=lambda a: a["delta_pct"], reverse=True)
    return {
        "alerts":       alerts,
        "current_rate": round(rate, 2),
        "rate_date":    rate_date,
        "error":        None,
    }
