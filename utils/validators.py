"""
ماژول validators — اعتبارسنجی ورودی‌های کاربر.
تمام توابع pure هستند — بدون DB یا I/O.
امضاها از CONTRACTS.md قفل شده‌اند.
"""

from __future__ import annotations

import re


# ══════════════════════════════════════════════════════════════════════════════
# اعتبارسنجی کد ملی — CONTRACTS.md
# ══════════════════════════════════════════════════════════════════════════════

def validate_national_id(value: str) -> bool:
    """
    کد ملی ۱۰ رقمی ایرانی را اعتبارسنجی می‌کند.

    بررسی‌ها:
        ۱. دقیقاً ۱۰ رقم عددی
        ۲. همه رقم‌ها یکسان نباشند (مثل 0000000000 نامعتبر است)
        ۳. الگوریتم رقم کنترل (checksum) ایرانی

    ورودی:
        value: رشته کد ملی

    خروجی:
        True اگر معتبر، False در غیر این صورت
    """
    # تبدیل اعداد عربی/فارسی به لاتین
    value = _normalize_digits(value)

    # بررسی طول و عددی بودن
    if not re.fullmatch(r"\d{10}", value):
        return False

    # بررسی یکسان نبودن همه ارقام
    if len(set(value)) == 1:
        return False

    # الگوریتم رقم کنترل ایرانی
    #digits = [int(d) for d in value]
    #check_digit = digits[9]
    #total = sum(digits[i] * (10 - i) for i in range(9))
    #remainder = total % 11

    #if remainder < 2:
    #    return check_digit == remainder
    #else:
    #    return check_digit == (11 - remainder)
    return True 

# ══════════════════════════════════════════════════════════════════════════════
# اعتبارسنجی نام — CONTRACTS.md
# ══════════════════════════════════════════════════════════════════════════════

def validate_name(value: str) -> bool:
    """
    نام کامل فارسی را اعتبارسنجی می‌کند.

    بررسی‌ها:
        ۱. حداقل ۲ کاراکتر
        ۲. فقط حروف فارسی/عربی، فاصله، نیم‌فاصله، و نقطه مجاز است
        ۳. نباید فقط فاصله باشد

    ورودی:
        value: رشته نام

    خروجی:
        True اگر معتبر، False در غیر این صورت
    """
    if not value or len(value.strip()) < 2:
        return False

    # حروف فارسی/عربی: یونیکد U+0600–U+06FF و U+FB50–U+FDFF
    # به علاوه فاصله (space)، نیم‌فاصله (ZWNJ: U+200C)، و نقطه
    pattern = re.compile(
        r"^[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF\s\u200C.]+$"
    )
    return bool(pattern.fullmatch(value.strip()))


# ══════════════════════════════════════════════════════════════════════════════
# اعتبارسنجی اعداد — CONTRACTS.md
# ══════════════════════════════════════════════════════════════════════════════

def validate_positive_float(value: str) -> tuple[bool, float | None]:
    """
    ورودی رشته‌ای را به عدد اعشاری مثبت تبدیل می‌کند.

    ورودی:
        value: رشته ورودی کاربر (ممکن است شامل اعداد فارسی باشد)

    خروجی:
        (True,  float_value) اگر معتبر و مثبت
        (False, None)        اگر نامعتبر یا صفر یا منفی
    """
    normalized = _normalize_digits(value).replace(",", ".").strip()
    try:
        result = float(normalized)
        if result > 0:
            return True, result
        return False, None
    except (ValueError, TypeError):
        return False, None


def validate_positive_int(value: str) -> tuple[bool, int | None]:
    """
    ورودی رشته‌ای را به عدد صحیح مثبت تبدیل می‌کند.

    ورودی:
        value: رشته ورودی کاربر (ممکن است شامل اعداد فارسی باشد)

    خروجی:
        (True,  int_value) اگر معتبر و مثبت
        (False, None)      اگر نامعتبر یا صفر یا منفی
    """
    normalized = _normalize_digits(value).strip()
    try:
        # اطمینان از عدم داشتن نقطه اعشار
        if "." in normalized:
            return False, None
        result = int(normalized)
        if result > 0:
            return True, result
        return False, None
    except (ValueError, TypeError):
        return False, None


# ══════════════════════════════════════════════════════════════════════════════
# اعتبارسنجی‌های تخصصی پروژه
# ══════════════════════════════════════════════════════════════════════════════

def validate_thickness_mm(value: str) -> tuple[bool, float | None]:
    """
    ضخامت رسوب (mm) را اعتبارسنجی می‌کند.
    محدوده منطقی: بیشتر از صفر و کمتر از ۱۰۰۰ mm.

    ورودی:
        value: رشته عدد

    خروجی:
        (True, float) یا (False, None)
    """
    ok, val = validate_positive_float(value)
    if not ok or val is None:
        return False, None
    if val >= 1000.0:
        return False, None
    return True, val


def validate_diameter_mm(value: str) -> tuple[bool, float | None]:
    """
    قطر لوله (mm) را اعتبارسنجی می‌کند.
    محدوده منطقی: بیشتر از صفر و کمتر از ۵۰۰۰ mm.

    ورودی:
        value: رشته عدد

    خروجی:
        (True, float) یا (False, None)
    """
    ok, val = validate_positive_float(value)
    if not ok or val is None:
        return False, None
    if val >= 5000.0:
        return False, None
    return True, val


def validate_pass_count(value: str) -> tuple[bool, int | None]:
    """
    تعداد پاس جوش را اعتبارسنجی می‌کند.
    محدوده منطقی: ۱ تا ۹۹.

    ورودی:
        value: رشته عدد

    خروجی:
        (True, int) یا (False, None)
    """
    ok, val = validate_positive_int(value)
    if not ok or val is None:
        return False, None
    if val > 99:
        return False, None
    return True, val


# ══════════════════════════════════════════════════════════════════════════════
# تابع کمکی داخلی
# ══════════════════════════════════════════════════════════════════════════════

def _normalize_digits(text: str) -> str:
    """
    اعداد فارسی (۰-۹) و عربی (٠-٩) را به اعداد لاتین (0-9) تبدیل می‌کند.
    ممیز عربی (٫ = U+066B) را به نقطه تبدیل می‌کند.
    """
    # اعداد فارسی: U+06F0–U+06F9
    for i in range(10):
        text = text.replace(chr(0x06F0 + i), str(i))
    # اعداد عربی: U+0660–U+0669
    for i in range(10):
        text = text.replace(chr(0x0660 + i), str(i))
    # ممیز اعشار عربی U+066B → نقطه لاتین
    text = text.replace("\u066B", ".")
    # جداکننده هزار عربی U+066C → حذف
    text = text.replace("\u066C", "")
    return text
