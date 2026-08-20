"""
قفل «در حال پردازش» برای جلوگیری از تداخل وقتی هوش مصنوعی مشغول است.

کاربر در میانهٔ گفتگو با AI دکمه‌ای می‌زند یا پیام می‌فرستد → به‌جای خطا،
پیام «⏳ هوش مصنوعی در حال بررسی است...» می‌گیرد.

امنیت:
  - قفل به ازای هر کاربر (telegram_id)
  - انقضای خودکار (پیش‌فرض ۹۰ ثانیه) تا اگر AI کرش کرد، قفل باز بماند
  - thread-safe (Lock)
"""

from __future__ import annotations

import threading
import time

_LOCK = threading.Lock()
_BUSY: dict[int, float] = {}  # telegram_id -> deadline (timestamp)
_DEFAULT_TTL = 90.0


def set_busy(telegram_id: int, ttl: float = _DEFAULT_TTL) -> None:
    """کاربر را «مشغول» علامت می‌زند."""
    with _LOCK:
        _BUSY[telegram_id] = time.monotonic() + ttl


def clear_busy(telegram_id: int) -> None:
    """قفل کاربر را برمی‌دارد."""
    with _LOCK:
        _BUSY.pop(telegram_id, None)


def is_busy(telegram_id: int) -> bool:
    """آیا کاربر در حال پردازش است؟ (با انقضای خودکار)"""
    with _LOCK:
        deadline = _BUSY.get(telegram_id)
        if deadline is None:
            return False
        if time.monotonic() > deadline:
            _BUSY.pop(telegram_id, None)
            return False
        return True
