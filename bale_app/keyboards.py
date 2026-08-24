"""
ماژول keyboards — سازنده‌های keyboard برای نسخه بله.
منوی اصلی: ثبت مشاهده، ثبت دانش، مشاهده‌های من، پروفایل من.
(پورت از handlers/keyboards.py — فقط منبع import تغییر کرده)
"""

from __future__ import annotations

from bale_app.framework import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """
    keyboard منوی اصلی.
    ورودی:
        user_id: شناسه کاربر (برای نمایش پروفایل)
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
