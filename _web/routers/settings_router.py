"""Settings router — signature configuration."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from models import fmt_money, fmt_date, norm
from routers.sig_utils import get_sig_settings, save_sig_setting, A4_W, A4_H

router = APIRouter(prefix="/settings")
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["fmt_money"] = fmt_money
templates.env.globals["fmt_date"]  = fmt_date
templates.env.globals["norm"]      = norm

# Directory where uploaded signature image is stored
UPLOADS_DIR = Path(__file__).parent.parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)
SIG_FILENAME = "signature"  # extension determined at upload time


def _current_sig_filename() -> str:
    """Return base filename of existing signature (with extension) or ''."""
    cfg = get_sig_settings()
    path = cfg.get("sig_path", "")
    if path and os.path.exists(path):
        return Path(path).name
    return ""


@router.get("", response_class=HTMLResponse)
async def settings_page(request: Request):
    cfg = get_sig_settings()
    sig_file = _current_sig_filename()
    return templates.TemplateResponse("settings.html", {
        "request":   request,
        "title":     "Налаштування",
        "current":   "/settings",
        "cfg":       cfg,
        "sig_file":  sig_file,
        "a4_w":      A4_W,
        "a4_h":      A4_H,
    })


@router.post("/signature/upload")
async def upload_signature(sig_file: UploadFile = File(...)):
    """Upload a PNG/JPG/SVG signature image."""
    allowed = {".png", ".jpg", ".jpeg", ".svg"}
    ext = Path(sig_file.filename).suffix.lower()
    if ext not in allowed:
        return HTMLResponse("Дозволені формати: PNG, JPG, SVG", status_code=400)

    # Remove old signature files
    for old in UPLOADS_DIR.glob(f"{SIG_FILENAME}.*"):
        old.unlink(missing_ok=True)

    dest = UPLOADS_DIR / f"{SIG_FILENAME}{ext}"
    content = await sig_file.read()
    dest.write_bytes(content)

    save_sig_setting("sig_path", str(dest))
    return RedirectResponse("/settings", status_code=303)


@router.post("/signature/position")
async def save_signature_position(
    sig_x:    float = Form(310),
    sig_y:    float = Form(55),
    sig_w:    float = Form(180),
    sig_h:    float = Form(70),
    sig_page: str   = Form("last"),
):
    """Save signature position and size settings."""
    for key, val in [
        ("sig_x",    str(sig_x)),
        ("sig_y",    str(sig_y)),
        ("sig_w",    str(sig_w)),
        ("sig_h",    str(sig_h)),
        ("sig_page", sig_page),
    ]:
        save_sig_setting(key, val)
    return RedirectResponse("/settings", status_code=303)


@router.post("/signature/delete")
async def delete_signature():
    """Remove the current signature image."""
    cfg = get_sig_settings()
    path = cfg.get("sig_path", "")
    if path and os.path.exists(path):
        os.unlink(path)
    save_sig_setting("sig_path", "")
    return RedirectResponse("/settings", status_code=303)
