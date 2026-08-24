"""
زیرمنوی «بایگانی و جستجو» — نسخه تلگرام.

مسیر: منوی اصلی → 🗂️ بایگانی و جستجو → (مشاهدات | دانش)
- بایگانی مشاهدات: از فلوی موجود observations استفاده می‌کند (obs:list / obs:search)
- بایگانی دانش: فلوهای kn:list / kn:search در handlers/knowledge.py
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler

logger = logging.getLogger(__name__)


def _archive_submenu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗂️ بایگانی مشاهدات", callback_data="archive:obs")],
        [InlineKeyboardButton("📚 بایگانی دانش/تجربه سازمانی", callback_data="archive:kn")],
        [InlineKeyboardButton("🏠 بازگشت به منو", callback_data="menu:main")],
    ])


def _obs_archive_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 نمایش مشاهده‌های من", callback_data="obs:list")],
        [InlineKeyboardButton("🔍 جستجو در مشاهدات", callback_data="obs:search")],
        [InlineKeyboardButton("↩️ بازگشت", callback_data="archive:open")],
    ])


def _kn_archive_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 نمایش دانش‌های من", callback_data="kn:list")],
        [InlineKeyboardButton("🔍 جستجو در دانش‌ها", callback_data="kn:search")],
        [InlineKeyboardButton("↩️ بازگشت", callback_data="archive:open")],
    ])


async def archive_open(update, context) -> None:
    """🗂️ بایگانی و جستجو — زیرمنوی اصلی."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🗂️ *بایگانی و جستجو*\n\nکدام بخش را می‌خواهید مرور کنید؟",
        parse_mode="Markdown",
        reply_markup=_archive_submenu_keyboard(),
    )


async def archive_obs(update, context) -> None:
    """بایگانی مشاهدات — انتخاب نمایش یا جستجو."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🗂️ *بایگانی مشاهدات*\n\nچه کاری انجام دهیم؟",
        parse_mode="Markdown",
        reply_markup=_obs_archive_keyboard(),
    )


async def archive_kn(update, context) -> None:
    """بایگانی دانش — انتخاب نمایش یا جستجو."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📚 *بایگانی دانش/تجربه سازمانی*\n\nچه کاری انجام دهیم؟",
        parse_mode="Markdown",
        reply_markup=_kn_archive_keyboard(),
    )


def get_archive_handlers() -> list[CallbackQueryHandler]:
    """هندلرهای standalone زیرمنو — در main.py ثبت می‌شوند."""
    return [
        CallbackQueryHandler(archive_open, pattern=r"^archive:open$"),
        CallbackQueryHandler(archive_obs, pattern=r"^archive:obs$"),
        CallbackQueryHandler(archive_kn, pattern=r"^archive:kn$"),
    ]
