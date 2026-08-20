"""
ماژول auth ساده برای MAPNAMD1-knowledge.
کاربر باید از طریق /start ثبت‌نام کند (فرم معرفی).
تا ثبت‌نام کامل نشده، دسترسی به منو ندارد.
"""

from __future__ import annotations

import functools
import logging
from typing import Callable

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from db.models import get_user_by_telegram_id

logger = logging.getLogger(__name__)


def is_registered(telegram_id: int) -> bool:
    """بررسی می‌کند آیا کاربر در DB ثبت شده است."""
    return get_user_by_telegram_id(telegram_id) is not None


async def _deny(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """پیام «دسترسی غیرمجاز» را به کاربر ارسال می‌کند."""
    text = "⛔ شما ثبت‌نام نکرده‌اید.\nلطفاً ابتدا /start را بزنید و ثبت‌نام را کامل کنید."
    if update.callback_query:
        await update.callback_query.answer(text, show_alert=True)
    elif update.message:
        await update.message.reply_text(text)


def require_registration(func: Callable) -> Callable:
    """
    دکوراتور: فقط کاربران ثبت‌شده مجاز هستند.
    اگر کاربر ثبت‌نام نکرده باشد → پیام خطا و توقف.
    """
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user or not is_registered(user.id):
            await _deny(update, context)
            return ConversationHandler.END
        return await func(update, context, *args, **kwargs)
    return wrapper