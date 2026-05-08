# DocketPro CRM — Технічна специфікація

> Версія: 1.0 · Дата: 07.05.2026 · Автор: Дмитро Барабин

---

## 1. Продукт

**DocketPro CRM** — локальна веб-система управління договорами, рахунками та актами для юридичної/IT-компанії. Запускається на `localhost:8000`, дані зберігаються в SQLite, документи (PDF/DOCX) — у локальній файловій системі.

Система замінює попередній tkinter-десктоп і надає зручний веб-інтерфейс з тими самими бізнес-правилами.

### Ключові бізнес-процеси, які автоматизує система

| Процес | Суть |
|---|---|
| Договори | Два типи: **Доступ** (SaaS-підписка, тариф × користувачі × місяці) та **Погодинний** (проєктні роботи, ставка × години) |
| Рахунки | Генерація, відстеження оплати, нагадування |
| Акти | Акти виконаних робіт, статуси підписання |
| Звірка з банком | Автоматичне зіставлення транзакцій Monobank з відкритими рахунками |
| Аналітика | MRR/ARR, борги, ROI, тренди доходу vs витрат |
| Документи | Генерація DOCX → PDF через LibreOffice, кастомні шаблони |

---

## 2. Стек технологій

| Шар | Технологія | Версія/Примітка |
|---|---|---|
| Backend | FastAPI (Python) | Async ASGI |
| База даних | SQLite 3 | WAL mode, foreign keys ON |
| Шаблони | Jinja2 | через FastAPI/Starlette |
| Frontend реактивність | HTMX 1.9.12 | CDN, часткові оновлення |
| Графіки | Chart.js 4.4.2 | CDN |
| Шрифти | Inter (Google Fonts) | |
| Word-документи | python-docx | XML-level заміна плейсхолдерів |
| PDF-конвертація | LibreOffice headless | `/Applications/LibreOffice.app` (macOS) |
| Банківська інтеграція | Monobank Open API | MONO_TOKEN з .env |
| Excel-експорт | openpyxl | |
| Сервер | uvicorn | `--reload --port 8000` |
| Оточення | Python venv | `/Users/dmytrobarabin/Documents/03_DOCKETPRO/CRM/venv` |

---

## 3. Структура директорій

```
CRM/
├── crm.db                          ← SQLite база даних
├── TECHSPEC.md                     ← цей документ
├── venv/                           ← Python venv
│
├── _web/                           ← Web-застосунок
│   ├── main.py                     ← FastAPI app, монтування роутерів
│   ├── database.py                 ← init_db(), get_db() context manager
│   ├── models.py                   ← norm(), fmt_money(), make_xlsx(), тощо
│   ├── utils.py                    ← бізнес-розрахунки (суми, дати, словами)
│   ├── word_handler.py             ← генерація DOCX + виклик LibreOffice
│   ├── config.py                   ← шляхи, змінні оточення
│   ├── migrate_excel.py            ← одноразова міграція зі старого Excel
│   │
│   ├── routers/
│   │   ├── dashboard.py            ← Дашборд + KPI
│   │   ├── clients.py              ← Клієнти CRUD
│   │   ├── contracts.py            ← Договори CRUD + статистика
│   │   ├── invoices.py             ← Рахунки CRUD + генерація PDF
│   │   ├── acts.py                 ← Акти CRUD + генерація PDF
│   │   ├── expenses.py             ← Витрати CRUD
│   │   ├── payments.py             ← Звірка з Monobank
│   │   ├── app_payments.py         ← Позадоговірні надходження
│   │   └── doc_templates.py        ← Управління шаблонами DOCX
│   │
│   ├── templates/                  ← Jinja2 HTML-шаблони
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   ├── clients.html / client_form.html
│   │   ├── contracts.html / contract_form.html
│   │   ├── expenses.html
│   │   ├── payments.html
│   │   ├── app_payments.html
│   │   ├── templates.html / template_editor.html
│   │   ├── doc_editor.html         ← Редактор документів з live-preview
│   │   ├── invoices/
│   │   │   ├── list.html / form.html / edit.html / done.html
│   │   │   └── editing.html
│   │   ├── acts/
│   │   │   ├── list.html / form.html / edit.html / done.html
│   │   │   └── editing.html
│   │   └── docs/                   ← HTML-preview для рахунків/актів
│   │       ├── invoice_access.html / invoice_hourly.html
│   │       └── act_access.html / act_hourly.html
│   │
│   └── static/
│       ├── styles.css              ← Design system (CSS змінні, компоненти)
│       └── app.css                 ← Специфічні стилі застосунку
│
├── _templates/                     ← Word-шаблони для документів
│   ├── Шаблон_Рахунок_Доступ.docx
│   ├── Шаблон_Рахунок_Погодинно.docx
│   ├── Шаблон_Акт_Доступ.docx
│   ├── Шаблон_Акт_Погодинно.docx
│   ├── Шаблон_Лист_Нагадування.docx
│   └── [кастомні шаблони клієнтів]
│
└── Договори/                       ← Сховище PDF-документів
    └── {Клієнт}/
        └── {№ договору}/
            ├── Рахунки/
            │   ├── Рахунок_{invoice_no}.pdf
            │   └── Нагадування_{invoice_no}.pdf
            └── Акти/
                └── Акт_{act_no}.pdf
```

---

## 4. База даних

**Файл:** `/Users/dmytrobarabin/Documents/03_DOCKETPRO/CRM/crm.db`

### Таблиця `clients`

| Колонка | Тип | Опис |
|---|---|---|
| `edrpou` | TEXT PK | ЄДРПОУ — унікальний ідентифікатор |
| `name` | TEXT | Повна назва організації |
| `director` | TEXT | ПІБ директора (для підписів в актах) |
| `email` | TEXT | Email |
| `phone` | TEXT | Телефон |
| `address` | TEXT | Юридична адреса |
| `status` | TEXT | `Активний` / `Неактивний` |

### Таблиця `contracts`

| Колонка | Тип | Опис |
|---|---|---|
| `contract_no` | TEXT PK | № договору (довільний формат) |
| `edrpou` | TEXT FK | → clients.edrpou |
| `client_name` | TEXT | Денормалізована назва клієнта |
| `contract_date` | TEXT | Дата укладання (dd.mm.yyyy) |
| `contract_end` | TEXT | Дата закінчення (dd.mm.yyyy) |
| `currency` | TEXT | `UAH` / `USD` / `EUR` |
| `tariff_fx` | REAL | Тариф у валюті договору |
| `type_rate` | TEXT | Тип курсу (`Продаж міжбанк` тощо) |
| `users` | INTEGER | Кількість користувачів (для Доступ) |
| `status` | TEXT | `Активний` / `Призупинено` / `Завершено` / `Закінчено` |
| `contract_type` | TEXT | `Доступ` / `Погодинний` |
| `subject` | TEXT | Предмет договору |
| `hour_rate` | REAL | Погодинна ставка (для Погодинний) |
| `pdf_path` | TEXT | Шлях до скану договору |
| `nbu_rate` | REAL | Курс НБУ |
| `act_template` | TEXT | Назва кастомного DOCX-шаблону акту |

### Таблиця `invoices`

| Колонка | Тип | Опис |
|---|---|---|
| `id` | INTEGER PK | Auto |
| `invoice_no` | TEXT UNIQUE | Формат: `RAH-ddmmyyyy-NNN` |
| `contract_no` | TEXT FK | → contracts.contract_no |
| `client_name` | TEXT | Денормалізована назва |
| `invoice_date` | TEXT | Дата рахунку |
| `fx_rate` | REAL | Курс валюти на дату рахунку |
| `currency` | TEXT | UAH / USD / EUR |
| `sum_fx` | REAL | Сума у валюті договору |
| `sum_uah` | REAL | Сума в гривнях |
| `period_from` | TEXT | Початок розрахункового періоду |
| `period_to` | TEXT | Кінець розрахункового періоду |
| `due_date` | TEXT | Кінцева дата оплати |
| `pay_status` | TEXT | `Не оплачено` / `Оплачено` / `Скасовано` |
| `pay_date` | TEXT | Дата фактичної оплати |
| `invoice_type` | TEXT | `Доступ` / `Погодинний` |
| `months` | INTEGER | Кількість місяців (для Доступ) |
| `sum_words` | TEXT | Сума словами (прописом) |
| `pdf_path` | TEXT | Абсолютний шлях до PDF |
| `discount_pct` | REAL | Знижка % (default 0) |
| `reminder_date` | TEXT | Дата відправки нагадування |
| `reminder_pdf_path` | TEXT | Шлях до PDF нагадування |

### Таблиця `acts`

| Колонка | Тип | Опис |
|---|---|---|
| `id` | INTEGER PK | Auto |
| `act_no` | TEXT UNIQUE | Формат: `ACT-ddmmyyyy-NNN` |
| `invoice_no` | TEXT FK | → invoices.invoice_no |
| `contract_no` | TEXT | Денормалізований № договору |
| `client_name` | TEXT | Денормалізована назва |
| `act_date` | TEXT | Дата акту |
| `period_from` | TEXT | Початок послуг |
| `period_to` | TEXT | Кінець послуг |
| `sum_uah` | REAL | Сума в гривнях |
| `status` | TEXT | `Чернетка` / `Надіслано` / `Підписано` / `Скасовано` |
| `pdf_path` | TEXT | Абсолютний шлях до PDF |

### Таблиця `expenses`

| Колонка | Тип | Опис |
|---|---|---|
| `id` | INTEGER PK | Auto |
| `exp_date` | TEXT | Дата витрати |
| `category` | TEXT | Категорія (Дизайн, Юрпослуги, тощо) |
| `description` | TEXT | Опис |
| `amount` | REAL | Сума у валюті |
| `currency` | TEXT | грн / USD / EUR |
| `exchange_rate` | REAL | Курс конвертації |
| `amount_uah` | REAL | Сума в гривнях |

### Таблиця `app_payments`

| Колонка | Тип | Опис |
|---|---|---|
| `id` | INTEGER PK | Auto |
| `pay_date` | TEXT | Дата |
| `amount` | REAL | Сума |
| `currency` | TEXT | UAH |
| `description` | TEXT | Опис |
| `source` | TEXT | `lyqpay` / `Приват24` / `Банк` / `Готівка` / `Інше` |

---

## 5. Модулі та функціонал

### 5.1 Дашборд (`/`)

**Що показує:**
- Кількість активних договорів і клієнтів
- Дохід за обраний період (виставлено / оплачено)
- Поточний борг (прострочено + в строку)
- Витрати (розбиття по категоріям)
- Позадоговірні надходження (LiqPay, банк)
- ROI = (дохід − витрати) / витрати × 100%
- Стрічка активності (5 останніх подій)
- Наступні рахунки до виставлення (активні Доступ-договори)
- Акти що потребують підписання (overdue)
- Договори що закінчуються (60-денне вікно)
- Тренд-графік: 6 місяців доходу vs витрат (Chart.js)

**Фільтри (query params):** `period` (month/week/quarter/year), `client`, `ctype`

---

### 5.2 Клієнти (`/clients`)

**CRUD операції:** список → перегляд → редагування → видалення (каскадне)

**Список:** пошук по назві/ЄДРПОУ, кнопка Excel-експорту

**Форма:** назва, ЄДРПОУ, директор, email, телефон, адреса, статус

---

### 5.3 Договори (`/contracts`)

**Список:**
- Статус-вкладки: Всі / Активний / Призупинено / Завершено / Чернетка / Закінчуються (≤30 днів)
- Live-пошук + фільтр типу (Доступ/Погодинний)
- Перемикач Таблиця / Картки
- Сортування колонок (клік по заголовку ↑↓)

**Статистика в заголовку:**
- `N активних · MRR ₴X · ARR ₴X · N погодинних · Борг ₴X`

**Таблиця:** № договору, клієнт, тип, сума/міс, кор., початок, діє до, борг, статус

**Картки:** компактний вигляд з основними даними

**Drawer (деталі):** статус + тип, усі поля договору, stats (рахунки всього / несплачено / актів), кнопки: Новий рахунок, Рахунки →, Редагувати

**Дані з БД:** борг рахується SQL-підзапитом `SUM(invoices.sum_uah WHERE pay_status NOT IN ('Оплачено','Скасовано'))`

**Формула MRR:** `SUM(tariff_fx × users)` для всіх активних Доступ-договорів

**Excel-експорт:** всі поля + борг + рахунки/акти

---

### 5.4 Рахунки (`/invoices`)

**Нумерація:** `RAH-{ddmmyyyy}-{NNN}` — автоматично, NNN = кількість рахунків за день + 1

**Тип Доступ:**
- Сума = `tariff_uah × users × months × (1 − discount%)`
- Курс: tariff_fx × fx_rate (UAH залишається без конвертації)
- Строк: дата рахунку + 5 днів

**Тип Погодинний:**
- Сума = `hours × hour_rate`
- Строк: сьогодні + 5 днів

**Статуси:** Не оплачено → Оплачено / Скасовано

**Фільтри:** статус, клієнт (по назві), рядковий пошук, рік

**Drawer (деталі):** всі поля, PDF-посилання, нагадування (дата + PDF), кнопки дій

**Нагадування:**
- Кнопка на рядку: генерує Лист_Нагадування, конвертує в PDF, зберігає `reminder_date` + `reminder_pdf_path`
- Індикатор ✉ + дата в колонці статусу

**Excel-експорт:** всі поля рахунку

**Генерація PDF:**
1. Заповнюється `Шаблон_Рахунок_*.docx` через `fill_template()`
2. DOCX → PDF через LibreOffice headless
3. PDF зберігається у `Договори/{client}/{contract}/Рахунки/`
4. `pdf_path` записується в БД

---

### 5.5 Акти (`/acts`)

**Нумерація:** `ACT-{ddmmyyyy}-{NNN}`

**Статуси:** Чернетка → Надіслано → Підписано / Скасовано

**Кастомні шаблони:**
- Поле `act_template` в договорі визначає який DOCX-шаблон використовувати
- Якщо не задано — стандартний (Доступ або Погодинний)
- HTML-preview показує стандартний макет + жовтий банер якщо кастомний шаблон

**Фільтри:** статус, клієнт, рядковий пошук

**Excel-експорт:** всі поля акту

---

### 5.6 Витрати (`/expenses`)

**CRUD:** список + форма додавання + видалення

**Категорії:** Дизайн, Юридичні витрати, Технічна підтримка, Розробка ПЗ, Реклама та маркетинг

**Мультивалютність:** amount + currency + exchange_rate → amount_uah

**Фільтри:** date_from, date_to, category

**Excel-експорт** ✓

---

### 5.7 Звірка з банком (`/payments`)

**Джерело:** Monobank Open API (токен з .env)

**Алгоритм зіставлення транзакцій:**
1. **Exact match** — `invoice_no` зустрічається в призначенні платежу
2. **Fuzzy match** — назва клієнта з відстанню Левенштейна
3. **EDRPOU match** — код ЄДРПОУ у призначенні
4. **LiqPay auto-save** — lyqpay/liqpay транзакції автозберігаються в `app_payments`

**Дедуплікація:** по `(pay_date, amount)`

**Дії:** відмітити оплаченим (одиночно або групово)

---

### 5.8 LiqPay / Позадоговірні надходження (`/app-payments`)

**CRUD:** список + форма + видалення

**Джерела:** lyqpay, Приват24, Банк, Готівка, Інше

**Фільтри:** date_from, date_to, source

---

### 5.9 Шаблони документів (`/templates`)

**Перегляд:**
- Список всіх `.docx` в `_templates/`
- Класифікація за типом: рахунок / акт / лист (по назві файлу)
- Доступні змінні для кожного типу
- Захищені стандартні шаблони (не можна видалити)

**Завантаження:**
- Upload `.docx`
- Вибір категорії → автоматичний префікс назви: `Шаблон_Акт_`, `Шаблон_Рахунок_`, `Шаблон_Лист_`

**Редактор в браузері:**
- Текстовий редактор з A4-макетом
- Панель змінних (клік → вставити `{{ЗМІННА}}`)
- Синтаксис: `{{ЗМІННА}}` для підстановки, `# H1` / `## H2` для заголовків
- Збереження → генерує `.docx` через python-docx

**Призначення до договору:**
- Drawer шаблону показує які договори використовують цей шаблон
- Можна призначити/зняти через кнопки прямо з `/templates`

---

## 6. Генерація документів

### Пайплайн

```
Форма (параметри)
    ↓
Python: складання dict replacements {{{ЗМІННА}}: значення}
    ↓
fill_template(template_path, replacements, filename)
    ├── читає DOCX через python-docx
    ├── XML-level заміна плейсхолдерів (зберігає гіперпосилання)
    └── зберігає .docx у /tmp/crm_docketpro/
          ↓
convert_to_pdf(docx_path, pdf_output_dir)
    ├── LibreOffice headless --convert-to pdf
    └── повертає шлях до PDF
          ↓
Переміщення PDF до Договори/{client}/{contract}/...
    ↓
UPDATE invoices/acts SET pdf_path = ...
```

### Змінні шаблонів

| Група | Змінні |
|---|---|
| Документ | `{{INVOICE_NO}}`, `{{ACT_NO}}`, `{{CONTRACT_NO}}`, `{{CONTRACT_DATE}}`, `{{INVOICE_DATE}}`, `{{ACT_DATE}}` |
| Клієнт | `{{CLIENT_NAME}}`, `{{CLIENT_CODE}}`, `{{CLIENT_ADDRESS}}`, `{{CLIENT_DIRECTOR}}` |
| Дати | `{{PERIOD_FROM}}`, `{{PERIOD_TO}}`, `{{DUE_DATE}}`, `{{DUE_LINE}}`, `{{TODAY_DATE}}` |
| Послуги | `{{SERVICE_SUBJECT}}`, `{{USERS}}`, `{{MONTHS}}`, `{{HOURS}}`, `{{HOUR_RATE}}`, `{{TARIFF}}` |
| Сума | `{{SUM}}`, `{{SUM_WORDS}}`, `{{DISCOUNT_LINE}}` |
| Підпис | `{{OUR_NAME}}` |

> `{{DUE_LINE}}` — смарт-змінна: автоматично формулює "Термін оплати минув X днів тому" або "Термін оплати: DATE"

---

## 7. Конфігурація

**Файл:** `_web/config.py`

```python
LIBREOFFICE_PATH  = "/Applications/LibreOffice.app/Contents/MacOS/soffice"  # macOS
TEMPLATES_DIR     = "_templates/"
CONTRACTS_DIR     = "Договори/"
TMP_DIR           = "/tmp/crm_docketpro"
```

**Змінні оточення (`.env`):**
```
MONO_TOKEN=...   # Monobank API token
MONO_IBAN=...    # IBAN рахунку для відбору транзакцій
```

**Запуск:**
```bash
cd /Users/dmytrobarabin/Documents/03_DOCKETPRO/CRM/_web
source ../venv/bin/activate
uvicorn main:app --reload --port 8000
```

---

## 8. Поточний стан функціоналу

| Модуль | Статус | Примітки |
|---|---|---|
| Дашборд | ✅ Повністю | KPI, графік, активність, прогнози |
| Клієнти | ✅ Повністю | CRUD + Excel |
| Договори | ✅ Повністю | CRUD + MRR/ARR + таблиця/картки + сортування |
| Рахунки (список) | ✅ Повністю | Фільтри, drawer, нагадування |
| Рахунки (генерація PDF) | ✅ Повністю | Доступ + Погодинний |
| Акти (список) | ✅ Повністю | Фільтри, статуси |
| Акти (генерація PDF) | ✅ Повністю | Кастомні шаблони |
| Нагадування | ✅ Повністю | Шаблон + генерація + позначка |
| Витрати | ✅ Повністю | CRUD + фільтри + Excel |
| Звірка з банком | ✅ Повністю | Monobank API + auto-match |
| LiqPay / App Payments | ✅ Повністю | CRUD + фільтри |
| Шаблони | ✅ Повністю | Upload + редактор + призначення до договору |
| Excel-експорт | ✅ Повністю | Всі модулі (виправлено UTF-8 header) |
| PDF-перегляд | ✅ Повністю | Через `/docs/` StaticFiles |
| Мобільний інтерфейс | ⚠️ Частково | Sidebar не адаптований |
| Мультикористувач | ❌ Відсутнє | Один локальний користувач, немає auth |

---

## 9. Відомі обмеження

1. **LibreOffice** — повинен бути встановлений локально. На macOS шлях захардкоджений.
2. **Один користувач** — немає автентифікації, авторизації, ролей.
3. **Локальний запуск** — SQLite не підходить для мережевого доступу з кількох машин.
4. **PDF-шлях** — абсолютний шлях у БД (`pdf_path`), не переносний між машинами.
5. **Мобільний** — sidebar фіксований, не responsive.
6. **Backup** — немає вбудованого резервного копіювання БД.
7. **Нумерація актів** — `ACT-ddmmyyyy-NNN` генерується окремо від `RAH-*` для рахунків, теоретично можлива колізія при паралельному записі (SQLite UNIQUE constraint захищає).

---

## 10. Дорожня карта (можливий розвиток)

### Пріоритет A — Практична цінність

| # | Задача | Опис |
|---|---|---|
| A1 | **Email-відправка** | Надсилати рахунок/акт/нагадування напряму клієнту через SMTP (Gmail / SendGrid). Прикріпляти PDF, шаблон тексту листа. |
| A2 | **Автовиставлення рахунків** | Cron-подібна логіка: у перший день місяця автоматично створювати рахунки для всіх активних Доступ-договорів. Підтвердження через UI. |
| A3 | **Нотифікації у браузері** | Web Push або polling: сповіщення коли підходить термін оплати, договір закінчується. |
| A4 | **Сторінка клієнта** | Окрема сторінка `/clients/{edrpou}` з повною історією: договори, рахунки, акти, платежі, борг. |
| A5 | **Bulk-дії в рахунках** | Чекбокси + "Відмітити оплаченими", "Скасувати" для кількох рахунків одночасно. |
| A6 | **Теги/нотатки до клієнта** | Вільний текст-нотатки до картки клієнта. |

### Пріоритет B — Аналітика та звіти

| # | Задача | Опис |
|---|---|---|
| B1 | **Звіт по клієнту** | PDF-звіт: всі рахунки/акти за клієнтом за період. |
| B2 | **Прогноз надходжень** | На основі активних договорів — очікуваний дохід наступних 3/6/12 місяців. |
| B3 | **Дебіторська заборгованість** | Ageing report: 0-30 / 31-60 / 61-90 / 90+ днів прострочення. |
| B4 | **Профіт по договору** | Дохід від договору мінус витрати пов'язані з клієнтом. |
| B5 | **Порівняння місяців** | Таблиця: кожен місяць vs попередній (рахунків, суми, оплачено). |

### Пріоритет C — Технічні покращення

| # | Задача | Опис |
|---|---|---|
| C1 | **Автентифікація** | Проста login-форма + JWT або session cookie. Підготовка до мережевого доступу. |
| C2 | **Docker-контейнер** | Dockerfile + LibreOffice всередині. Запуск без ручного встановлення залежностей. |
| C3 | **Переносний PDF-шлях** | Зберігати відносний шлях (від `CONTRACTS_DIR`), не абсолютний. |
| C4 | **Автобекап БД** | Щоденний `sqlite3 .backup` до окремої папки (або GitHub Gist). |
| C5 | **Responsive sidebar** | Hamburger-меню для мобільних / планшетів. |
| C6 | **Пошук по всій системі** | Глобальний пошук: клієнт / № рахунку / № договору / ЄДРПОУ — в одному полі. |
| C7 | **Audit log** | Таблиця `audit_log` (хто, що, коли) для відстеження змін. |
| C8 | **PostgreSQL міграція** | Перехід з SQLite на PostgreSQL при потребі мережевого доступу. |

### Пріоритет D — Інтеграції

| # | Задача | Опис |
|---|---|---|
| D1 | **ПриватБанк API** | Доповнення до Monobank: звірка транзакцій ПриватБанку. |
| D2 | **М.Е.Doc / СОТА** | Електронний документообіг: відправка актів в ЕДО. |
| D3 | **Telegram-бот** | Сповіщення: "Рахунок RAH-... оплачено", "Договір ... закінчується за 7 днів". |
| D4 | **QR-код на рахунку** | QR з реквізитами для оплати. |
| D5 | **Calendly / Google Calendar** | Синхронізація дат закінчення договорів з календарем. |

---

## 11. Критичні залежності

```
# requirements.txt (основні)
fastapi
uvicorn[standard]
jinja2
python-multipart          # для Form() в FastAPI
python-docx               # генерація Word
openpyxl                  # Excel-експорт
httpx                     # Monobank API запити
python-dotenv             # .env файл

# Системні залежності
LibreOffice 7+            # PDF-конвертація (встановлюється окремо)
```

---

## 12. Структура коду — ключові функції

### `word_handler.py`

```python
fill_template(template_path, replacements, out_filename) → str
    # XML-level заміна {{PLACEHOLDER}} в DOCX, зберігає гіперпосилання
    # Повертає абсолютний шлях до .docx у TMP_DIR

convert_to_pdf(docx_path, pdf_output_path) → str
    # LibreOffice headless конвертація
    # Повертає шлях до PDF

generate_invoice_access(data: dict) → str   # → pdf_path
generate_invoice_hourly(data: dict) → str
generate_act_access(data: dict) → str
generate_act_hourly(data: dict) → str
generate_act_with_template(data, template_path) → str  # кастомний шаблон
generate_reminder(data: dict) → str         # лист-нагадування

build_invoice_pdf_path(client_name, contract_no, invoice_no) → str
build_act_pdf_path(client_name, contract_no, act_no) → str
build_reminder_pdf_path(client_name, contract_no, invoice_no) → str
```

### `models.py`

```python
norm(value) → str              # "active" → "Активний"
parse_date(val) → date | None  # парсить будь-який формат дати
fmt_date(d) → str              # → "dd.mm.yyyy"
fmt_money(val) → str           # → "18 684,00"
is_overdue(status, due_date) → bool
pdf_url(abs_path) → str        # → "/docs/..." URL
make_xlsx(headers, rows, sheet) → BytesIO
```

### `utils.py`

```python
amount_to_words_uah(amount) → str    # 18684 → "вісімнадцять тисяч..."
count_months(date_from, date_to) → int
calc_access_tariff_uah(tariff_fx, currency, fx_rate) → float
calc_access_sum(users, months, tariff_uah) → float
calc_hourly_sum(hours, hour_rate) → float
calc_due_date_access(invoice_date) → date  # +5 днів
```

### `database.py`

```python
init_db()     # CREATE TABLE IF NOT EXISTS + ALTER TABLE міграції
get_db()      # context manager → sqlite3.Connection (Row factory)
```

---

*Документ створено автоматично на основі аудиту кодової бази станом на 07.05.2026.*
