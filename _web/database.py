"""
SQLite connection helper for DocketPro CRM web.
Provides init_db() and get_db() context manager.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "crm.db"

CREATE_TABLES_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS clients (
    edrpou      TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    director    TEXT,
    email       TEXT,
    phone       TEXT,
    address     TEXT,
    status      TEXT DEFAULT 'Активний'
);

CREATE TABLE IF NOT EXISTS contracts (
    contract_no   TEXT PRIMARY KEY,
    edrpou        TEXT REFERENCES clients(edrpou),
    client_name   TEXT,
    contract_date TEXT,
    contract_end  TEXT,
    currency      TEXT DEFAULT 'UAH',
    tariff_fx     REAL,
    type_rate     TEXT,
    users         INTEGER DEFAULT 1,
    status        TEXT DEFAULT 'Активний',
    contract_type TEXT,
    subject       TEXT,
    hour_rate     REAL,
    pdf_path      TEXT,
    nbu_rate      REAL,
    act_template  TEXT
);

CREATE TABLE IF NOT EXISTS invoices (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_no    TEXT UNIQUE NOT NULL,
    contract_no   TEXT REFERENCES contracts(contract_no),
    client_name   TEXT,
    invoice_date  TEXT,
    fx_rate       REAL,
    currency      TEXT,
    sum_fx        REAL,
    sum_uah       REAL,
    period_from   TEXT,
    period_to     TEXT,
    due_date      TEXT,
    pay_status    TEXT DEFAULT 'Не оплачено',
    pay_date      TEXT,
    invoice_type  TEXT,
    months        INTEGER,
    users         INTEGER DEFAULT 1,
    sum_words     TEXT,
    pdf_path      TEXT,
    discount_pct  REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS acts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    act_no        TEXT UNIQUE NOT NULL,
    invoice_no    TEXT REFERENCES invoices(invoice_no),
    contract_no   TEXT,
    client_name   TEXT,
    act_date      TEXT,
    period_from   TEXT,
    period_to     TEXT,
    sum_uah       REAL,
    status        TEXT DEFAULT 'Draft',
    pdf_path      TEXT
);

CREATE TABLE IF NOT EXISTS expenses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    exp_date      TEXT,
    category      TEXT,
    description   TEXT,
    amount        REAL,
    currency      TEXT DEFAULT 'грн',
    exchange_rate REAL DEFAULT 1,
    amount_uah    REAL
);

CREATE TABLE IF NOT EXISTS app_payments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pay_date    TEXT,
    amount      REAL,
    currency    TEXT DEFAULT 'UAH',
    description TEXT,
    source      TEXT
);

CREATE TABLE IF NOT EXISTS expense_categories (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS nbu_rate_cache (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_ts INTEGER NOT NULL,
    rate       REAL    NOT NULL,
    rate_date  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS contract_amendments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_no    TEXT NOT NULL,
    du_no          TEXT NOT NULL,
    sign_date      TEXT,
    effective_date TEXT,
    users          INTEGER,
    tariff_fx      REAL,
    contract_end   TEXT,
    notes          TEXT,
    pdf_path       TEXT
);
"""

_DEFAULT_EXPENSE_CATEGORIES = [
    "Дизайн",
    "Юридичні витрати",
    "Технічна підтримка",
    "Розробка програмного забезпечення",
    "Реклама та маркетинг",
]


def init_db():
    """Create all tables if they do not exist, and run any pending migrations."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(CREATE_TABLES_SQL)
        # Migrations — ADD COLUMN is idempotent via try/except
        for ddl in [
            "ALTER TABLE invoices   ADD COLUMN reminder_date      TEXT",
            "ALTER TABLE invoices   ADD COLUMN reminder_pdf_path  TEXT",
            "ALTER TABLE invoices   ADD COLUMN email_sent_date    TEXT",
            "ALTER TABLE invoices   ADD COLUMN users              INTEGER DEFAULT 1",
            "ALTER TABLE acts       ADD COLUMN email_sent_date    TEXT",
            "ALTER TABLE contracts  ADD COLUMN nbu_tracking       INTEGER DEFAULT 0",
            "ALTER TABLE contracts  ADD COLUMN nbu_threshold_pct  REAL DEFAULT 5.0",
        ]:
            try:
                conn.execute(ddl)
            except Exception:
                pass
        # Seed default expense categories if the table is empty
        count = conn.execute("SELECT COUNT(*) FROM expense_categories").fetchone()[0]
        if count == 0:
            conn.executemany(
                "INSERT OR IGNORE INTO expense_categories (name) VALUES (?)",
                [(c,) for c in _DEFAULT_EXPENSE_CATEGORIES],
            )


@contextmanager
def get_db():
    """Yield a sqlite3 connection with row_factory set to dict rows."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
