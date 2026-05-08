# DocketPro CRM

Веб-додаток для управління договорами, рахунками та актами DocketPro.  
Запускається локально на `localhost:8000`.

---

## Стек технологій

| Шар | Технологія |
|---|---|
| Backend | Python 3.9 + FastAPI |
| База даних | SQLite (`crm.db`) |
| Шаблони | Jinja2 |
| CSS / UI | Tailwind CSS (CDN) |
| Реактивність | HTMX (CDN) |
| Графіки | Chart.js (CDN) |
| Word-документи | python-docx |
| PDF-конвертація | LibreOffice headless |
| Excel-експорт | openpyxl |
| Банк API | Monobank API |

---

## Структура проєкту

```
CRM/
├── .env                    ← секрети: MONO_TOKEN, MONO_IBAN
├── crm.db                  ← база даних SQLite
├── venv/                   ← Python virtual environment
├── CRM_DOCKETPRO_2026.xlsx ← вихідні дані (резервна копія)
│
├── _templates/             ← Word-шаблони документів
│   ├── Шаблон_Рахунок_Доступ.docx
│   ├── Шаблон_Рахунок_Погодинно.docx
│   ├── Шаблон_Акт_Доступ.docx
│   └── Шаблон_Акт_Погодинно.docx
│
├── Договори/               ← згенеровані PDF-документи клієнтів
│   └── {Клієнт}/
│       └── Договір {№}/
│           ├── Рахунки/    ← PDF рахунків
│           └── Акти/       ← PDF актів
│
└── _web/                   ← веб-додаток
    ├── main.py             ← точка входу FastAPI
    ├── config.py           ← шляхи, константи, env-змінні
    ├── database.py         ← SQLite підключення, ініціалізація схеми
    ├── models.py           ← хелпери: norm(), fmt_money(), pdf_url(), make_xlsx()
    ├── utils.py            ← бізнес-логіка: розрахунки, дати, сума прописом
    ├── word_handler.py     ← генерація Word + PDF через LibreOffice
    ├── mono_client.py      ← Monobank API клієнт
    ├── migrate_excel.py    ← одноразовий імпорт з Excel → SQLite
    │
    ├── routers/
    │   ├── dashboard.py    ← KPI, графіки, борги, наступні рахунки
    │   ├── clients.py      ← CRUD клієнтів
    │   ├── contracts.py    ← CRUD договорів
    │   ├── invoices.py     ← рахунки: створення, Word/PDF, оплата
    │   ├── acts.py         ← акти: створення, Word/PDF, підписання
    │   ├── expenses.py     ← витрати
    │   └── payments.py     ← банківська виписка Приват24 / Monobank
    │
    ├── templates/          ← Jinja2 HTML-шаблони
    │   ├── base.html
    │   ├── dashboard.html
    │   ├── clients.html
    │   ├── contracts.html
    │   ├── expenses.html
    │   ├── invoices/
    │   │   ├── list.html
    │   │   ├── form.html
    │   │   ├── editing.html  ← проміжна сторінка "Word відкрито"
    │   │   └── done.html
    │   └── acts/
    │       ├── list.html
    │       ├── form.html
    │       ├── editing.html
    │       └── done.html
    │
    └── static/             ← CSS override, іконки
```

---

## Запуск

```bash
cd /Users/dmytrobarabin/Documents/03_DOCKETPRO/CRM/_web
source ../venv/bin/activate
uvicorn main:app --reload --port 8000
```

Відкрити: **http://localhost:8000**

Якщо порт зайнятий:
```bash
lsof -ti :8000 | xargs kill -9
```

---

## Залежності

```bash
cd _web
pip install -r requirements.txt
```

Основні пакети: `fastapi`, `uvicorn`, `jinja2`, `python-multipart`, `openpyxl`,  
`python-docx`, `num2words`, `python-dateutil`, `httpx`, `python-dotenv`

---

## База даних

SQLite-файл `crm.db` у корені проєкту. Схема ініціалізується автоматично при запуску (`init_db()`).

| Таблиця | Опис |
|---|---|
| `clients` | Клієнти (ЄДРПОУ, назва, директор, адреса) |
| `contracts` | Договори (тип: Доступ / Погодинний, тариф, строк) |
| `invoices` | Рахунки (сума, статус, строк оплати, PDF-шлях) |
| `acts` | Акти виконаних робіт (статус, PDF-шлях) |
| `expenses` | Витрати (категорія, сума, валюта) |
| `app_payments` | Оплати через додаток (Lyqpay та ін.) |

---

## Основні функції

### Дашборд
- KPI за обраний період: виставлено / сплачено / борг / витрати / ROI
- Графік помісячного доходу (Chart.js)
- Таблиця всіх рахунків зі статусами (прострочені — вгорі)
- Список рахунків без актів
- Список підписантів актів
- Попередження про договори що закінчуються (≤60 днів)
- Фільтри за датою, клієнтом, типом договору

### Рахунки
- Форма виставлення з live-розрахунком суми
- Кнопки швидкого вибору періоду (1/2/3/6/12 місяців)
- Підтримка знижки та курсу валюти
- Автоматична генерація Word → відкриття для редагування → конвертація в PDF
- PDF зберігається у `Договори/{Клієнт}/Договір {№}/Рахунки/`
- Inline зміна статусу (Сплачено / Скасувати)
- Excel-експорт з фільтрами
- Підсумки вгорі списку: оплачено / прострочено / не оплачено / всього

### Акти
- Вибір рахунку без акту
- Генерація Word за шаблоном договору (стандартний або кастомний)
- Той самий Word → редагування → PDF флоу
- Статуси: Чернетка → Направлений → Підписано
- Excel-експорт

### Договори / Клієнти
- Повний CRUD
- Попередження про закінчення терміну дії
- Cascade-видалення (клієнт → договори → рахунки → акти)
- Excel-експорт

### Банк / Платежі
- Завантаження CSV-виписки Приват24
- Підключення Monobank API (токен в `.env`)
- Автоматичне розпізнавання платежів

---

## Конфігурація (.env)

```ini
MONO_TOKEN=your_monobank_token
MONO_IBAN=UA...
```

Файл `.env` знаходиться у корені `CRM/`. **Не комітити в git.**

---

## Word-шаблони

Шаблони знаходяться в `_templates/`. Плейсхолдери у форматі `{{НАЗВА}}`.

**Рахунок:** `{{INVOICE_NO}}`, `{{CLIENT_NAME}}`, `{{CONTRACT_NO}}`, `{{PERIOD_FROM}}`, `{{PERIOD_TO}}`, `{{USERS}}`, `{{MONTHS}}`, `{{TARIFF}}`, `{{SUM}}`, `{{SUM_WORDS}}`, `{{DUE_DATE}}`

**Акт:** `{{ACT_NO}}`, `{{ACT_DATE}}`, `{{CLIENT_NAME}}`, `{{CLIENT_CODE}}`, `{{CLIENT_DIRECTOR}}`, `{{CONTRACT_NO}}`, `{{CONTRACT_DATE}}`, `{{PERIOD_FROM}}`, `{{PERIOD_TO}}`, `{{USERS}}`, `{{MONTHS}}`, `{{TARIFF}}`, `{{SUM}}`, `{{SUM_WORDS}}`

Кастомний шаблон акту вказується у полі `act_template` договору (ім'я файлу в `_templates/`).

---

## LibreOffice (PDF-конвертація)

Має бути встановлений LibreOffice:
```
/Applications/LibreOffice.app/Contents/MacOS/soffice
```

Шлях задається у `_web/config.py` → `LIBREOFFICE_PATH`.
