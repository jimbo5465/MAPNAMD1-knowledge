"""
ماژول dates — تبدیل تاریخ جلالی به میلادی و برعکس، محاسبه انقضا.
از jdatetime==4.1.1 استفاده می‌شود (پیش‌نیاز requirements.txt).
تمام توابع pure هستند — بدون DB یا I/O.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

# تلاش برای import جدتایم — اگر نصب نباشد با پیام واضح خطا می‌دهد
try:
    import jdatetime  # type: ignore[import]
    _JDATETIME_AVAILABLE = True
except ImportError:
    _JDATETIME_AVAILABLE = False


def _require_jdatetime() -> None:
    """اطمینان از نصب jdatetime قبل از استفاده."""
    if not _JDATETIME_AVAILABLE:
        raise ImportError(
            "ماژول jdatetime نصب نیست. دستور زیر را اجرا کنید:\n"
            "pip install jdatetime==4.1.1"
        )


# ══════════════════════════════════════════════════════════════════════════════
# تبدیل جلالی → میلادی
# ══════════════════════════════════════════════════════════════════════════════

def jalali_to_gregorian(jalali_str: str) -> str:
    """
    تاریخ جلالی را به میلادی تبدیل می‌کند.

    ورودی:
        jalali_str: رشته تاریخ جلالی به فرمت 'YYYY/MM/DD' یا 'YYYY-MM-DD'
                    مثال: '1402/12/15' یا '1402-12-15'

    خروجی:
        رشته تاریخ میلادی به فرمت 'YYYY-MM-DD' (برای ذخیره در DB)

    خطا:
        ValueError اگر فرمت نامعتبر باشد
    """
    _require_jdatetime()
    try:
        # نرمال‌سازی جداکننده
        normalized = jalali_str.replace("/", "-")
        parts = normalized.split("-")
        if len(parts) != 3:
            raise ValueError()
        jy, jm, jd = int(parts[0]), int(parts[1]), int(parts[2])
        gregorian_date = jdatetime.date(jy, jm, jd).togregorian()
        return gregorian_date.strftime("%Y-%m-%d")
    except (ValueError, AttributeError, TypeError):
        raise ValueError(
            f"فرمت تاریخ جلالی '{jalali_str}' نامعتبر است. "
            f"فرمت صحیح: 'YYYY/MM/DD' یا 'YYYY-MM-DD'"
        )


def gregorian_to_jalali(gregorian_str: str) -> str:
    """
    تاریخ میلادی را به جلالی تبدیل می‌کند (برای نمایش به کاربر).

    ورودی:
        gregorian_str: رشته تاریخ میلادی به فرمت 'YYYY-MM-DD'

    خروجی:
        رشته تاریخ جلالی به فرمت 'YYYY/MM/DD'
        مثال: '1402/12/15'

    خطا:
        ValueError اگر فرمت نامعتبر باشد
    """
    _require_jdatetime()
    try:
        g_date = date.fromisoformat(gregorian_str)
        j_date = jdatetime.date.fromgregorian(date=g_date)
        return j_date.strftime("%Y/%m/%d")
    except (ValueError, AttributeError, TypeError):
        raise ValueError(
            f"فرمت تاریخ میلادی '{gregorian_str}' نامعتبر است. "
            f"فرمت صحیح: 'YYYY-MM-DD'"
        )


def jalali_to_display(jalali_str: str) -> str:
    """
    تاریخ جلالی را به فرمت نمایشی فارسی تبدیل می‌کند.

    ورودی:
        jalali_str: 'YYYY/MM/DD' یا 'YYYY-MM-DD'

    خروجی:
        رشته نمایشی مثل '۱۵ اسفند ۱۴۰۲'
    """
    _require_jdatetime()
    try:
        normalized = jalali_str.replace("/", "-")
        parts = normalized.split("-")
        jy, jm, jd = int(parts[0]), int(parts[1]), int(parts[2])
        j_date = jdatetime.date(jy, jm, jd)
        return j_date.strftime("%-d %B %Y")
    except (ValueError, AttributeError, TypeError, IndexError):
        raise ValueError(f"فرمت تاریخ '{jalali_str}' نامعتبر است.")


def gregorian_to_jalali_display(gregorian_str: str) -> str:
    """
    تاریخ میلادی را به فرمت نمایشی جلالی تبدیل می‌کند.

    ورودی:
        gregorian_str: 'YYYY-MM-DD'

    خروجی:
        رشته نمایشی فارسی مثل '۱۵ اسفند ۱۴۰۲'
    """
    _require_jdatetime()
    try:
        g_date = date.fromisoformat(gregorian_str)
        j_date = jdatetime.date.fromgregorian(date=g_date)
        return j_date.strftime("%-d %B %Y")
    except (ValueError, AttributeError, TypeError):
        raise ValueError(
            f"فرمت تاریخ میلادی '{gregorian_str}' نامعتبر است."
        )


# ══════════════════════════════════════════════════════════════════════════════
# محاسبه تاریخ انقضا
# ══════════════════════════════════════════════════════════════════════════════

def compute_expiry_date(test_date_gregorian: str, validity_years: int = 3) -> str:
    """
    تاریخ انقضای گواهینامه WQT را محاسبه می‌کند.

    ورودی:
        test_date_gregorian: تاریخ آزمون به فرمت میلادی 'YYYY-MM-DD'
        validity_years:      مدت اعتبار به سال (پیش‌فرض ۳ سال — ASME Sec. IX)

    خروجی:
        رشته تاریخ انقضا به فرمت میلادی 'YYYY-MM-DD' (برای ذخیره در DB)

    خطا:
        ValueError اگر فرمت نامعتبر باشد
    """
    try:
        g_date = date.fromisoformat(test_date_gregorian)
    except (ValueError, TypeError):
        raise ValueError(
            f"فرمت تاریخ آزمون '{test_date_gregorian}' نامعتبر است. "
            f"فرمت صحیح: 'YYYY-MM-DD'"
        )

    # محاسبه سال انقضا با رعایت کبیسه
    expiry_year = g_date.year + validity_years
    expiry_month = g_date.month
    expiry_day = g_date.day

    # رعایت ۲۹ فوریه در سال‌های غیرکبیسه
    try:
        expiry_date = date(expiry_year, expiry_month, expiry_day)
    except ValueError:
        # ۲۹ فوریه در سال غیر کبیسه → ۲۸ فوریه
        expiry_date = date(expiry_year, expiry_month, 28)

    return expiry_date.strftime("%Y-%m-%d")


def compute_expiry_from_jalali(jalali_test_date: str, validity_years: int = 3) -> str:
    """
    تاریخ انقضا را از تاریخ آزمون جلالی محاسبه می‌کند.

    ورودی:
        jalali_test_date: تاریخ آزمون به فرمت جلالی 'YYYY/MM/DD'
        validity_years:   مدت اعتبار به سال (پیش‌فرض ۳)

    خروجی:
        رشته تاریخ انقضا به فرمت میلادی 'YYYY-MM-DD'
    """
    gregorian_str = jalali_to_gregorian(jalali_test_date)
    return compute_expiry_date(gregorian_str, validity_years)


# ══════════════════════════════════════════════════════════════════════════════
# وضعیت اعتبار گواهینامه
# ══════════════════════════════════════════════════════════════════════════════

def days_until_expiry(expiry_date_gregorian: str) -> int:
    """
    تعداد روزهای باقیمانده تا انقضای گواهینامه را محاسبه می‌کند.
    مقایسه بر اساس تاریخ میلادی (فرمت ذخیره در DB).

    ورودی:
        expiry_date_gregorian: تاریخ انقضا به فرمت 'YYYY-MM-DD'

    خروجی:
        عدد صحیح — مثبت اگر هنوز معتبر، منفی اگر منقضی شده
    """
    try:
        expiry = date.fromisoformat(expiry_date_gregorian)
    except (ValueError, TypeError):
        raise ValueError(
            f"فرمت تاریخ انقضا '{expiry_date_gregorian}' نامعتبر است."
        )
    return (expiry - date.today()).days


def is_expired(expiry_date_gregorian: str) -> bool:
    """
    بررسی می‌کند که آیا گواهینامه منقضی شده است.

    ورودی:
        expiry_date_gregorian: تاریخ انقضا به فرمت 'YYYY-MM-DD'

    خروجی:
        True اگر منقضی شده، False اگر هنوز معتبر است
    """
    return days_until_expiry(expiry_date_gregorian) < 0


def qualification_status(expiry_date_gregorian: str, warning_days: int = 30) -> str:
    """
    وضعیت گواهینامه را به صورت رشته فارسی برمی‌گرداند.

    ورودی:
        expiry_date_gregorian: تاریخ انقضا به فرمت 'YYYY-MM-DD'
        warning_days:          روزهای هشدار قبل از انقضا (پیش‌فرض ۳۰)

    خروجی:
        'منقضی شده'     — اگر منقضی شده
        'رو به انقضا'   — اگر ظرف warning_days روز منقضی می‌شود
        'معتبر'         — اگر هنوز اعتبار کافی دارد
    """
    remaining = days_until_expiry(expiry_date_gregorian)
    if remaining < 0:
        return "منقضی شده"
    elif remaining <= warning_days:
        return "رو به انقضا"
    return "معتبر"


def validate_jalali_date_str(value: str) -> tuple[bool, str | None]:
    """
    رشته تاریخ جلالی را اعتبارسنجی می‌کند.

    ورودی:
        value: رشته ورودی از کاربر

    خروجی:
        (True, None)          اگر معتبر
        (False, error_msg)    اگر نامعتبر
    """
    if not _JDATETIME_AVAILABLE:
        # بدون jdatetime فقط فرمت را بررسی می‌کنیم
        normalized = value.replace("/", "-")
        parts = normalized.split("-")
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            return True, None
        return False, "فرمت تاریخ نامعتبر است. مثال: ۱۴۰۲/۰۱/۱۵"

    try:
        jalali_to_gregorian(value)
        return True, None
    except ValueError as e:
        return False, str(e)
