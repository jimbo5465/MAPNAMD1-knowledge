"""
تقویم جلالی inline برای نسخه تلگرام — انتخاب تاریخ با دکمه‌های شیشه‌ای.

منطق مشترک در utils/jalali_calendar_core.py است؛ این ماژول فقط تاپل‌های
(text, callback_data) را به telegram.InlineKeyboardMarkup تبدیل میکند.

هر flow با یک prefix اختصاصی استفاده میکند:
  - observations → prefix="obsdate"
  - knowledge    → prefix="kndate"

callback_data ها:
  {prefix}:open              ← دکمه «انتخاب از تقویم» (در فلو تعریف میشود)
  {prefix}:view:{y}:{m}      ← ناوبری ماه/سال
  {prefix}:pick:{y}:{m}:{d}  ← انتخاب روز
  {prefix}:none              ← سلول غیرفعال (عنوان ماه، خانه‌های خالی)
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from utils.jalali_calendar_core import (
    MIN_YEAR,
    MAX_YEAR,
    build_calendar_grid,
    days_in_jalali_month,
    parse_pick_data,
    parse_view_data,
)

__all__ = (
    "MIN_YEAR", "MAX_YEAR",
    "build_calendar_keyboard",
    "days_in_jalali_month",
    "parse_pick_data",
    "parse_view_data",
)


def build_calendar_keyboard(prefix: str, year: int, month: int) -> InlineKeyboardMarkup:
    """کیبورد تقویم برای ماه مشخص از سال مشخص."""
    rows = build_calendar_grid(prefix, year, month)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
        for row in rows
    ])
