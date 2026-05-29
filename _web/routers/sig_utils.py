"""Signature overlay utility — overlay a PNG/JPG signature onto an existing PDF."""
from __future__ import annotations

import io
import os

# A4 in PDF points (1 pt = 1/72 inch)
A4_W: float = 595.27
A4_H: float = 841.89

_DEFAULT: dict[str, str] = {
    "sig_path": "",
    "sig_x":    "310",   # points from left edge
    "sig_y":    "55",    # points from bottom edge
    "sig_w":    "180",   # width in points
    "sig_h":    "70",    # height in points
    "sig_page": "last",  # 'first' | 'last' | 'all'
}


def get_sig_settings() -> dict[str, str]:
    from database import get_db
    cfg = dict(_DEFAULT)
    try:
        with get_db() as db:
            rows = db.execute("SELECT key, value FROM settings").fetchall()
        for r in rows:
            cfg[r["key"]] = r["value"] or ""
    except Exception:
        pass
    return cfg


def save_sig_setting(key: str, value: str) -> None:
    from database import get_db
    with get_db() as db:
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def apply_signature_to_pdf(pdf_path: str) -> tuple[bool, str]:
    """Overlay the configured signature image onto the last (or configured) page of a PDF.

    Returns (True, '') on success or (False, error_message) on failure.
    Modifies the PDF file in-place.
    """
    try:
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.lib.utils import ImageReader
        import pypdf
    except ImportError as exc:
        return False, f"Бібліотека не встановлена: {exc}"

    cfg = get_sig_settings()
    sig_path = cfg.get("sig_path", "")

    if not sig_path:
        return False, "Зображення підпису не завантажено (Налаштування → Підпис)"
    if not os.path.exists(sig_path):
        return False, f"Файл підпису не знайдено: {sig_path}"
    if not pdf_path or not os.path.exists(pdf_path):
        return False, "PDF документа не знайдено — спочатку згенеруйте PDF"

    try:
        x = float(cfg.get("sig_x", _DEFAULT["sig_x"]))
        y = float(cfg.get("sig_y", _DEFAULT["sig_y"]))
        w = float(cfg.get("sig_w", _DEFAULT["sig_w"]))
        h = float(cfg.get("sig_h", _DEFAULT["sig_h"]))
        page_target = cfg.get("sig_page", "last")

        # Build in-memory overlay PDF (1 page, A4)
        packet = io.BytesIO()
        c = rl_canvas.Canvas(packet, pagesize=(A4_W, A4_H))
        c.drawImage(ImageReader(sig_path), x, y, width=w, height=h, mask="auto")
        c.save()
        packet.seek(0)

        sig_reader  = pypdf.PdfReader(packet)
        orig_reader = pypdf.PdfReader(pdf_path)
        writer      = pypdf.PdfWriter()

        n = len(orig_reader.pages)
        for i, page in enumerate(orig_reader.pages):
            is_target = (
                page_target == "all"
                or (page_target == "first" and i == 0)
                or (page_target == "last"  and i == n - 1)
            )
            if is_target:
                page.merge_page(sig_reader.pages[0])
            writer.add_page(page)

        with open(pdf_path, "wb") as f:
            writer.write(f)

        return True, ""

    except Exception as exc:
        return False, str(exc)
