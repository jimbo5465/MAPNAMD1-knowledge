"""
ماژول auth ساده برای نسخه بله.
کاربر باید از طریق /start ثبت‌نام کند (فرم معرفی).
تا ثبت‌نام کامل نشده، دسترسی به منو ندارد.
(پورت از handlers/auth.py — فقط منبع import تغییر کرده)
"""

from __future__ import annotations

import functools
import logging
from typing import Callable

from db.models import get_user_by_telegram_id
from bale_app.framework import ConversationHandler, Context, _Update

logger = logging.getLogger(__name__)


def is_registered(user_id: int) -> bool:
    """بررسی می‌کند آیا کاربر در DB ثبت شده است."""
    return get_user_by_telegram_id(user_id) is not None


async def _deny(update: _Update, context: Context) -> None:
    """پیام «دسترسی غیرمجاز» را به کاربر ارسال می‌کند."""
    text = "⛔ شما ثبت‌نام نکرده‌اید.\nلطفاً ابتدا /start را بزنید و ثبت‌نام را کامل کنید."
    if update.callback_query:
        # در API بله alert دکمه‌ای وجود ندارد — پیام ارسال می‌شود
        if update.callback_query.message:
            await update.callback_query.message.reply_text(text)
    elif update.message:
        await update.message.reply_text(text)


def require_registration(func: Callable) -> Callable:
    """
    دکوراتور: فقط کاربران ثبت‌شده مجاز هستند.
    اگر کاربر ثبت‌نام نکرده باشد → پیام خطا و توقف.
    """
    @functools.wraps(func)
    async def wrapper(update: _Update, context: Context, *args, **kwargs):
        user = update.effective_user
        if not user or not is_registered(user.id):
            await _deny(update, context)
            return ConversationHandler.END
        return await func(update, context, *args, **kwargs)
    return wrapper
