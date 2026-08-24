# -*- coding: utf-8 -*-
"""
بک‌اند مینی‌اپ وب — ربات دانش سازمانی MAPNAMD1.

همهٔ endpointها (به‌جز /api/auth) نیاز به هدر Authorization: Bearer <token> دارند.
توکن نشست فقط پس از اعتبارسنجی initData و تطبیق کاربر ثبت‌شده صادر می‌شود.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402,F401  — مسیر DB را مقداردهی می‌کند
from db.init import init_db  # noqa: E402
from db.models import (  # noqa: E402
    get_knowledge_entry_by_id,
    get_observation_attachment,
    get_observation_by_id,
    get_user_by_platform_id,
    list_knowledge_by_user,
    list_observation_attachments,
    list_observations_by_user,
    search_knowledge_by_user,
    search_observations,
)
from webapp.auth import (  # noqa: E402
    issue_session_token,
    resolve_session_token,
    validate_init_data,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webapp")

BALE_TOKENS = [t for t in [os.environ.get("BALE_BOT_TOKEN", "").strip()] if t]
TELEGRAM_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WEBAPP_SECRET = os.environ.get("WEBAPP_SECRET", "")

if not WEBAPP_SECRET:
    raise RuntimeError("متغیر محیطی WEBAPP_SECRET تنظیم نشده است.")

init_db()

app = FastAPI(title="MAPNAMD1 Knowledge MiniApp", docs_url=None, redoc_url=None)

PAGE_SIZE = 10


# ══════════════════════════════════════════════════════════════════════════════
# ابزار
# ══════════════════════════════════════════════════════════════════════════════

class AuthIn(BaseModel):
    init_data: str
    platform: str = "bale"


def current_db_user(authorization: str = Header(default="")) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    uid = resolve_session_token(token, WEBAPP_SECRET)
    if not uid:
        raise HTTPException(status_code=401, detail="نشست نامعتبر است.")
    user = get_user_by_platform_id(uid)
    if not user:
        raise HTTPException(status_code=401, detail="کاربر یافت نشد.")
    return user


def _owner_key(user: dict) -> int:
    """کلید مالکیت رکوردها برای کاربر."""
    return user["telegram_id"]


def _paginate(items: list, page: int) -> tuple[list, int]:
    pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * PAGE_SIZE
    return items[start:start + PAGE_SIZE], pages


def _kn_title(entry: dict) -> str:
    fields = entry.get("fields_json") or {}
    title = ""
    if isinstance(fields, dict):
        title = str(fields.get("title") or "").strip()
    if not title:
        draft = (entry.get("draft_text") or "").strip()
        raw = (entry.get("raw_description") or "").strip()
        title = draft or raw or "بدون عنوان"
    return " ".join(title.split())[:80]


_KN_TYPE_FA = {
    "lesson": "درس‌آموخته",
    "suggestion": "پیشنهاد بهبود",
    "explicit": "دانش صریح",
}


def _kn_item(e: dict) -> dict:
    created = (e.get("created_at") or "")[:10]
    return {
        "id": e["id"],
        "title": _kn_title(e),
        "type": _KN_TYPE_FA.get(e.get("knowledge_type"), "دانش"),
        "status": e.get("status"),
        "kn_number": e.get("kn_number"),
        "date": created,
    }


def _strip_voice_prefix(text: str | None) -> str:
    t = (text or "").lstrip()
    if t.startswith("📝 متن تشخیص"):
        colon = t.find(":", len("📝 متن تشخیص"))
        if 0 <= colon <= 80:
            t = t[colon + 1:]
    return t.strip()


def _obs_title(obs: dict) -> str:
    title = (obs.get("title") or "").strip()
    if not title:
        content = _strip_voice_prefix(obs.get("content") or "")
        title = content.splitlines()[0] if content else ""
    return " ".join(title.split())[:80]


def _obs_item(o: dict) -> dict:
    return {
        "id": o["id"],
        "title": _obs_title(o),
        "status": o.get("status"),
        "date": o.get("obs_date") or (o.get("created_at") or "")[:10],
    }


_STATUS_FA = {
    "raw": "خام",
    "maturing": "در حال بررسی",
    "promoted": "ارتقایافته به دانش",
    "archived": "بایگانی‌شده",
    "draft": "پیش‌نویس",
    "submitted": "ثبت‌شده",
}


# ══════════════════════════════════════════════════════════════════════════════
# احراز هویت
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/auth")
def auth(body: AuthIn):
    platform = (body.platform or "bale").lower()
    tokens = BALE_TOKENS if platform in ("bale", "bale-web") else (
        [TELEGRAM_TOKEN] if TELEGRAM_TOKEN else []
    )
    info = validate_init_data(body.init_data, tokens)
    if not info:
        raise HTTPException(status_code=401, detail="اعتبارسنجی ناموفق بود.")

    user = get_user_by_platform_id(info["platform_user_id"])
    if not user:
        raise HTTPException(
            status_code=403,
            detail="شما هنوز ثبت‌نام نکرده‌اید. ابتدا در ربات /start را بزنید.",
        )
    token = issue_session_token(user["id"], WEBAPP_SECRET)
    return {
        "token": token,
        "user": {
            "full_name": user["full_name"],
            "project_name": user.get("project_name"),
            "position": user.get("position"),
            "personnel_code": user.get("personnel_code"),
        },
    }


@app.get("/api/me")
def me(user: dict = Depends(current_db_user)):
    return {
        "full_name": user["full_name"],
        "phone": user.get("phone"),
        "personnel_code": user.get("personnel_code"),
        "project_name": user.get("project_name"),
        "position": user.get("position"),
    }


# ══════════════════════════════════════════════════════════════════════════════
# دانش‌ها
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/kn")
def kn_list(page: int = Query(0, ge=0), user: dict = Depends(current_db_user)):
    entries = list_knowledge_by_user(_owner_key(user))
    chunk, pages = _paginate(entries, page)
    return {"items": [_kn_item(e) for e in chunk], "total": len(entries), "pages": pages}


@app.get("/api/kn/search")
def kn_search(q: str = Query(..., min_length=1), user: dict = Depends(current_db_user)):
    entries = search_knowledge_by_user(_owner_key(user), q.strip())
    return {"items": [_kn_item(e) for e in entries], "total": len(entries), "pages": 1}


@app.get("/api/kn/{kid}")
def kn_detail(kid: int, user: dict = Depends(current_db_user)):
    entry = get_knowledge_entry_by_id(kid)
    if not entry or entry.get("reported_by") != user["id"]:
        raise HTTPException(status_code=404, detail="یافت نشد.")
    org = entry.get("org_metadata_json") or {}
    tags = org.get("hashtags") if isinstance(org, dict) else None
    tree = entry.get("tree_path_json") or []
    photos = json.dumps([])  # placeholder — شمارش عکس‌ها در صورت نیاز اضافه می‌شود
    desc = (entry.get("draft_text") or entry.get("raw_description") or "").strip()
    return {
        **_kn_item(entry),
        "description": desc[:6000],
        "tree_path": tree if isinstance(tree, list) else [],
        "hashtags": tags if isinstance(tags, list) else [],
        "pdf_available": bool(entry.get("pdf_path")),
    }


# ══════════════════════════════════════════════════════════════════════════════
# مشاهدات
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/obs")
def obs_list(page: int = Query(0, ge=0), user: dict = Depends(current_db_user)):
    items_all = list_observations_by_user(_owner_key(user))
    chunk, pages = _paginate(items_all, page)
    result = []
    for o in chunk:
        item = _obs_item(o)
        atts = list_observation_attachments(o["id"])
        item["attachments"] = [
            {"id": a["id"], "name": a.get("file_name"), "is_image": (a.get("mime_type") or "").startswith("image")}
            for a in atts
        ]
        result.append(item)
    return {"items": result, "total": len(items_all), "pages": pages}


@app.get("/api/obs/search")
def obs_search(q: str = Query(..., min_length=1), user: dict = Depends(current_db_user)):
    results = search_observations(_owner_key(user), keyword=q.strip())
    out = []
    for o in results:
        item = _obs_item(o)
        atts = list_observation_attachments(o["id"])
        item["attachments"] = [
            {"id": a["id"], "name": a.get("file_name"), "is_image": (a.get("mime_type") or "").startswith("image")}
            for a in atts
        ]
        out.append(item)
    return {"items": out, "total": len(results), "pages": 1}


@app.get("/api/obs/{oid}")
def obs_detail(oid: int, user: dict = Depends(current_db_user)):
    obs = get_observation_by_id(oid)
    if not obs or obs["telegram_id"] != _owner_key(user):
        raise HTTPException(status_code=404, detail="یافت نشد.")
    atts = list_observation_attachments(oid)
    return {
        **_obs_item(obs),
        "content": _strip_voice_prefix(obs.get("content") or ""),
        "tags": obs.get("tags") or "",
        "attachments": [
            {"id": a["id"], "name": a.get("file_name"), "is_image": (a.get("mime_type") or "").startswith("image")}
            for a in atts
        ],
    }


@app.get("/api/file/obs-att/{att_id}")
def obs_attachment_file(att_id: int, user: dict = Depends(current_db_user)):
    att = get_observation_attachment(att_id)
    if not att:
        raise HTTPException(status_code=404, detail="یافت نشد.")
    obs = get_observation_by_id(att["observation_id"])
    if not obs or obs["telegram_id"] != _owner_key(user):
        raise HTTPException(status_code=403, detail="دسترسی ندارید.")
    path = att.get("file_path") or ""
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="فایل روی سرور نیست.")
    return FileResponse(path, filename=os.path.basename(path))


@app.exception_handler(Exception)
async def unhandled(request, exc):  # noqa: ANN001
    logger.exception("خطای وب‌اپ")
    return JSONResponse(status_code=500, content={"detail": "خطای داخلی سرور."})
