# -*- coding: utf-8 -*-
"""
اعتبارسنجی initData مینی‌اپ‌ها (بله / تلگرام) و مدیریت توکن نشست.

الگوریتم (مطابق مستندات بله — همتای تلگرام):
    data_check_string = فیلدهای initData مرتب‌شده بر اساس کلید، جدا با \n (بدون hash)
    secret_key        = HMAC-SHA256(key="WebAppData", msg=bot_token)
    معتبر است اگر    = hex(HMAC-SHA256(key=secret_key, msg=data_check_string)) == hash

توکن نشست: payload JSON (base64url) + امضای HMAC با WEBAPP_SECRET — بدون نیاز به session DB.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import parse_qsl

_TOKEN_TTL_SECONDS = 7 * 86400
_INIT_DATA_MAX_AGE = 2 * 86400


def _hmac(key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, hashlib.sha256).digest()


def validate_init_data(init_data: str, bot_tokens: list[str] | str) -> dict[str, Any] | None:
    """
    initData را با هر یک از توکن‌های داده‌شده اعتبارسنجی می‌کند.
    خروجی: {"platform_user_id", "first_name", "username", "auth_date"} یا None
    """
    if isinstance(bot_tokens, str):
        bot_tokens = [bot_tokens]
    try:
        pairs = dict(parse_qsl(init_data or "", keep_blank_values=True))
    except Exception:
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))

    for token in bot_tokens:
        if not token:
            continue
        # ترتیب استاندارد تلگرام + حالت جایگشت‌داده‌شدهٔ مستندات (برای سازگاری)
        candidates = (
            _hmac(b"WebAppData", token.encode()),
            _hmac(token.encode(), b"WebAppData"),
        )
        for secret_key in candidates:
            calc = _hmac(secret_key, data_check_string.encode()).hex()
            if hmac.compare_digest(calc, received_hash):
                try:
                    auth_date = int(pairs.get("auth_date", "0"))
                except ValueError:
                    return None
                if auth_date <= 0 or time.time() - auth_date > _INIT_DATA_MAX_AGE:
                    return None
                user = {}
                try:
                    user = json.loads(pairs.get("user", "{}") or "{}")
                except json.JSONDecodeError:
                    return None
                uid = user.get("id")
                if not uid:
                    return None
                return {
                    "platform_user_id": int(uid),
                    "first_name": user.get("first_name") or "",
                    "username": user.get("username") or "",
                    "auth_date": auth_date,
                }
    return None


# ─── توکن نشست ────────────────────────────────────────────────────────────────

def _sign(payload_b64: bytes, secret: str) -> str:
    return _hmac(secret.encode(), payload_b64).hex()


def issue_session_token(db_user_id: int, secret: str) -> str:
    payload = {"uid": db_user_id, "exp": int(time.time()) + _TOKEN_TTL_SECONDS}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    b64 = base64.urlsafe_b64encode(raw).rstrip(b"=")
    sig = _sign(b64, secret)
    return f"{b64.decode()}.{sig}"


def resolve_session_token(token: str | None, secret: str) -> int | None:
    """توکن نشست را بررسی و شناسهٔ داخلی کاربر را برمی‌گرداند؛ نامعتبر → None."""
    if not token or "." not in token:
        return None
    try:
        b64_part, sig_part = token.rsplit(".", 1)
        b64 = b64_part.encode()
        if not hmac.compare_digest(_sign(b64, secret), sig_part):
            return None
        padded = b64 + b"=" * (-len(b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        if payload.get("exp", 0) < time.time():
            return None
        return int(payload["uid"])
    except Exception:
        return None
