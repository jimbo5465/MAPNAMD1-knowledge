"""
مدیریت پرامپت‌ها — پیام قبلی ربات قبل از نمایش پیام جدید حذف شود (نسخه تلگرام).

معادل منطق پیاده‌شده در bale_app/framework.py:
  - track_prompt: شناسهٔ آخرین پرامپت ذخیره میشود
  - delete_tracked: قبل از پرامپت جدید، قبلی پاک میشود
"""

from __future__ import annotations

from telegram.ext import ContextTypes


def track_prompt(context: ContextTypes.DEFAULT_TYPE, msg, key: str = "_bot_prompt") -> None:
    """شناسهٔ آخرین پیام پرامپت ربات را برای حذف بعدی ذخیره میکند."""
    try:
        context.user_data[key] = {"chat": msg.chat_id, "id": msg.message_id}
    except Exception:
        pass


async def delete_tracked(context: ContextTypes.DEFAULT_TYPE, key: str = "_bot_prompt") -> None:
    """پیام پرامپت ذخیره‌شده را پاک میکند (اگر باشد؛ خطا نادیده گرفته میشود)."""
    info = context.user_data.pop(key, None)
    if not info:
        return
    try:
        await context.bot.delete_message(info["chat"], info["id"])
    except Exception:
        pass


async def prompt_reply(update_or_msg, context, text, reply_markup=None):
    """پرامپت قبلی حذف، پیام جدید ارسال و track میشود — چت تمیز میماند."""
    await delete_tracked(context)
    sent = await update_or_msg.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    track_prompt(context, sent)
    return sent
