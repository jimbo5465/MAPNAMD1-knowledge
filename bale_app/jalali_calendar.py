"""
تقویم جلالی inline برای نسخه بله — انتخاب تاریخ با دکمه‌های شیشه‌ای.

هر flow با یک prefix اختصاصی از این ماژول استفاده میکند:
  - observations → prefix="obsdate"
  - knowledge    → prefix="kndate"

callback_data ها:
  {prefix}:open              ← دکمه «انتخاب از تقویم» (در فلو تعریف میشود)
  {prefix}:view:{y}:{m}      ← ناوبری ماه/سال
  {prefix}:pick:{y}:{m}:{d}  ← انتخاب روز
  {prefix}:none              ← سلول غیرفعال (عنوان ماه، خانه‌های خالی)

هفته شنبه‌محور است (weekday() در jdatetime: ۰=شنبه … ۶=جمعه).
"""

from __future__ import annotations

import jdatetime

from bale_app.framework import InlineKeyboardButton, InlineKeyboardMarkup

MONTHS_FA = (
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
)
WEEKDAYS_SHORT = ("ش", "ی", "د", "س", "چ", "پ", "ج")

MIN_YEAR = 1380
MAX_YEAR = 1450


def days_in_jalali_month(year: int, month: int) -> int:
    """طول ماه جلالی — اسفند کبیسه ۳۰ روز."""
    if month <= 6:
        return 31
    if month <= 11:
        return 30
    try:
        jdatetime.date(year, 12, 30)
        return 30
    except ValueError:
        return 29


def _nav_button(prefix: str, label: str, year: int, month: int) -> InlineKeyboardButton:
    if MIN_YEAR <= year <= MAX_YEAR and 1 <= month <= 12:
        return InlineKeyboardButton(label, callback_data=f"{prefix}:view:{year}:{month}")
    return InlineKeyboardButton("·", callback_data=f"{prefix}:none")


def build_calendar_keyboard(prefix: str, year: int, month: int) -> InlineKeyboardMarkup:
    """کیبورد تقویم برای ماه مشخص از سال مشخص."""
    today = jdatetime.date.today()
    rows = []

    # ── ناوبری ──
    prev_y, prev_m = (year, month - 1) if month > 1 else (year - 1, 12)
    next_y, next_m = (year, month + 1) if month < 12 else (year + 1, 1)

    rows.append([
        _nav_button(prefix, "◀️", prev_y, prev_m),
        InlineKeyboardButton(f"{MONTHS_FA[month - 1]} {year}", callback_data=f"{prefix}:none"),
        _nav_button(prefix, "▶️", next_y, next_m),
    ])
    rows.append([
        _nav_button(prefix, "⏪ سال قبل", year - 1, month),
        _nav_button(prefix, "سال بعد ⏩", year + 1, month),
    ])

    # ── سربرگ روزهای هفته ──
    rows.append([
        InlineKeyboardButton(w, callback_data=f"{prefix}:none")
        for w in WEEKDAYS_SHORT
    ])

    # ── شبکهٔ روزها ──
    first_weekday = jdatetime.date(year, month, 1).weekday()  # ۰=شنبه
    n_days = days_in_jalali_month(year, month)

    week: list[InlineKeyboardButton] = [
        InlineKeyboardButton(" ", callback_data=f"{prefix}:none")
        for _ in range(first_weekday)
    ]
    for day in range(1, n_days + 1):
        is_today = (year, month, day) == (today.year, today.month, today.day)
        label = f"[{day}]" if is_today else str(day)
        week.append(
            InlineKeyboardButton(label, callback_data=f"{prefix}:pick:{year}:{month}:{day}")
        )
        if len(week) == 7:
            rows.append(week)
            week = []
    if week:
        week.extend(
            InlineKeyboardButton(" ", callback_data=f"{prefix}:none")
            for _ in range(7 - len(week))
        )
        rows.append(week)

    return InlineKeyboardMarkup(rows)


def parse_view_data(data: str) -> tuple[int, int] | None:
    """'{pre}:view:{y}:{m}' → (y, m) یا None اگر نامعتبر/خارج از محدوده."""
    parts = (data or "").split(":")
    if len(parts) != 4 or parts[1] != "view":
        return None
    try:
        year, month = int(parts[2]), int(parts[3])
    except ValueError:
        return None
    if not (MIN_YEAR <= year <= MAX_YEAR and 1 <= month <= 12):
        return None
    return year, month


def parse_pick_data(data: str) -> tuple[int, int, int] | None:
    """'{pre}:pick:{y}:{m}:{d}' → (y, m, d) یا None؛ اعتبار روز هم چک میشود."""
    parts = (data or "").split(":")
    if len(parts) != 5 or parts[1] != "pick":
        return None
    try:
        year, month, day = int(parts[2]), int(parts[3]), int(parts[4])
    except ValueError:
        return None
    if not (MIN_YEAR <= year <= MAX_YEAR and 1 <= month <= 12):
        return None
    if not (1 <= day <= days_in_jalali_month(year, month)):
        return None
    return year, month, day
