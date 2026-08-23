"""
هستهٔ مشترک تقویم جلالی — مستقل از پلتفرم (تلگرام/بله).

خروجی build_calendar_grid ردیف‌هایی از تاپل‌های (text, callback_data) است
که آداپتور هر پلتفرم به کلاس دکمهٔ خودش تبدیل میکند:
  - handlers/jalali_calendar.py  → telegram.InlineKeyboardButton
  - bale_app/jalali_calendar.py  → bale_app.framework.InlineKeyboardButton

callback_data ها:
  {prefix}:view:{y}:{m}      ← ناوبری ماه/سال
  {prefix}:pick:{y}:{m}:{d}  ← انتخاب روز
  {prefix}:none              ← سلول غیرفعال

هفته شنبه‌محور است (weekday() در jdatetime: ۰=شنبه … ۶=جمعه).
"""

from __future__ import annotations

import jdatetime

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


def build_calendar_grid(prefix: str, year: int, month: int) -> list[list[tuple[str, str]]]:
    """گرید تقویم ماه مشخص — ردیف‌هایی از (text, callback_data)."""
    today = jdatetime.date.today()
    none_data = f"{prefix}:none"
    rows: list[list[tuple[str, str]]] = []

    def nav_btn(label: str, y: int, m: int) -> tuple[str, str]:
        if MIN_YEAR <= y <= MAX_YEAR and 1 <= m <= 12:
            return label, f"{prefix}:view:{y}:{m}"
        return "·", none_data

    prev_y, prev_m = (year, month - 1) if month > 1 else (year - 1, 12)
    next_y, next_m = (year, month + 1) if month < 12 else (year + 1, 1)

    # ناوبری
    rows.append([
        nav_btn("◀️", prev_y, prev_m),
        (f"{MONTHS_FA[month - 1]} {year}", none_data),
        nav_btn("▶️", next_y, next_m),
    ])
    rows.append([
        nav_btn("⏪ سال قبل", year - 1, month),
        nav_btn("سال بعد ⏩", year + 1, month),
    ])

    # سربرگ روزهای هفته
    rows.append([(w, none_data) for w in WEEKDAYS_SHORT])

    # شبکهٔ روزها
    first_weekday = jdatetime.date(year, month, 1).weekday()  # ۰=شنبه
    n_days = days_in_jalali_month(year, month)

    week: list[tuple[str, str]] = [(" ", none_data)] * first_weekday
    for day in range(1, n_days + 1):
        is_today = (year, month, day) == (today.year, today.month, today.day)
        week.append((
            f"[{day}]" if is_today else str(day),
            f"{prefix}:pick:{year}:{month}:{day}",
        ))
        if len(week) == 7:
            rows.append(week)
            week = []
    if week:
        week.extend([(" ", none_data)] * (7 - len(week)))
        rows.append(week)

    return rows


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
