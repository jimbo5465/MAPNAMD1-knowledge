"""
ماژول keyboards — سازنده‌های keyboard برای MAPNAMD1-knowledge.
منوی اصلی: ثبت مشاهده، ثبت دانش، بایگانی و جستجو، پروفایل من.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    """
    keyboard منوی اصلی.
    ورودی:
        telegram_id: شناسه تلگرام کاربر (برای نمایش پروفایل)
    """
    buttons = [
        [InlineKeyboardButton("📓 ثبت مشاهده", callback_data="obs:new")],
        [InlineKeyboardButton("📝 ثبت دانش/تجربه سازمانی", callback_data="kn:new")],
        [InlineKeyboardButton("🗂️ بایگانی و جستجو", callback_data="archive:open")],
        [InlineKeyboardButton("👤 پروفایل من", callback_data="profile:view")],
    ]
    return InlineKeyboardMarkup(buttons)


def back_to_main_keyboard() -> InlineKeyboardMarkup:
    """دکمه بازگشت به منوی اصلی."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 بازگشت به منو", callback_data="menu:main")]
    ])


def profile_keyboard() -> InlineKeyboardMarkup:
    """دکمه‌های پروفایل: ویرایش، بازگشت."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ ویرایش پروفایل", callback_data="profile:edit")],
        [InlineKeyboardButton("🏠 بازگشت به منو", callback_data="menu:main")],
    ])
