import os
from dotenv import load_dotenv

# ============================================================
# ШЛЯХИ — BASE_DIR визначається автоматично відносно цього файлу
# ============================================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Завантажуємо .env з кореня проєкту
load_dotenv(os.path.join(BASE_DIR, ".env"))

EXCEL_PATH      = os.path.join(BASE_DIR, "CRM_DOCKETPRO_2026.xlsx")
TEMPLATES_DIR   = os.path.join(BASE_DIR, "_templates")
CONTRACTS_DIR   = os.path.join(BASE_DIR, "Договори")  # куди зберігаються PDF

# Шаблони
TEMPLATE_INVOICE_ACCESS  = os.path.join(TEMPLATES_DIR, "Шаблон_Рахунок_Доступ.docx")
TEMPLATE_INVOICE_HOURLY  = os.path.join(TEMPLATES_DIR, "Шаблон_Рахунок_Погодинно.docx")
TEMPLATE_ACT_ACCESS      = os.path.join(TEMPLATES_DIR, "Шаблон_Акт_Доступ.docx")
TEMPLATE_ACT_HOURLY      = os.path.join(TEMPLATES_DIR, "Шаблон_Акт_Погодинно.docx")

# Формати дат
DATE_FORMAT = "%d.%m.%Y"

# LibreOffice — шлях для конвертації в PDF (macOS)
LIBREOFFICE_PATH = "/Applications/LibreOffice.app/Contents/MacOS/soffice"

# Тимчасова папка для docx перед збереженням
TMP_DIR = "/tmp/crm_docketpro"

# API Monobank — зчитуємо з .env (не зберігати в коді!)
MONO_TOKEN = os.getenv("MONO_TOKEN", "")
MONO_IBAN  = os.getenv("MONO_IBAN", "")

# ── SMTP / Email ─────────────────────────────────────────────────
# Встановити в .env:
#   SMTP_HOST     — smtp.gmail.com       (Gmail)
#                   smtp.ukr.net         (ukr.net)
#                   smtp.sendgrid.net    (SendGrid)
#   SMTP_PORT     — 587  (STARTTLS, більшість провайдерів)
#                   465  (SSL, ukr.net / meta.ua)
#   SMTP_USER     — логін або email відправника
#   SMTP_PASSWORD — пароль або app-password (Gmail з 2FA)
#   EMAIL_FROM    — адреса у полі «From» (може = SMTP_USER)
#   EMAIL_FROM_NAME — відображувана назва відправника
SMTP_HOST       = os.getenv("SMTP_HOST", "")
SMTP_PORT       = os.getenv("SMTP_PORT", "587")
SMTP_USER       = os.getenv("SMTP_USER", "")
SMTP_PASSWORD   = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM      = os.getenv("EMAIL_FROM", "")
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", "DocketPro CRM")