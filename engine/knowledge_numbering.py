"""
engine/knowledge_numbering.py
تولید شمارهٔ یکتای رکورد دانش/تجربه بر اساس الگوی:

    <کد پروژه>-KN-<سال شمسی>-<سریال>

مثال:
    PAR-KN-1405-001

قوانین:
  - سال شمسی از jdatetime گرفته می‌شود.
  - سریال به ازای هر (پروژه، سال) از ۱ شروع و ۰-padding سه رقمی می‌شود.
  - یکتایی شماره با UNIQUE constraint جدول knowledge_entries تضمین می‌شود.
  - این ماژول فقط از db.models و db.init می‌خواند؛ هیچ وابستگی به تلگرام ندارد.
"""

from __future__ import annotations

import jdatetime

from db.init import get_connection


def jalali_year() -> int:
    """سال جاری شمسی."""
    return jdatetime.datetime.now().year


def project_code(project_id: int | None) -> str:
    """
    کد کوتاه پروژه برای شمارهٔ دانش.
    در MAPNAMD1-knowledge پروژه به صورت متن آزاد است و project_id همیشه None
    است — پس کد عمومی «KN» برمیگردد.
    """
    if project_id is None:
        return "KN"
    # سازگاری با کد قبلی — اگر روزی project_id معنادار شد
    return "KN"


def max_serial_for_project(project_id: int | None) -> int:
    """
    بزرگ‌ترین سریال سال جاری برای این پروژه (یا عمومی اگر None) را از روی
    kn_number های موجود (به فرمت <code>-KN-<year>-<serial>) پیدا میکند.
    اگر هیچ رکوردی نباشد ۰.
    """
    rows = _fetch_existing_numbers(project_code(project_id))
    max_serial = 0
    for num in rows:
        try:
            serial = int(num.rsplit("-", 1)[-1])
            max_serial = max(max_serial, serial)
        except (ValueError, IndexError):
            continue
    return max_serial


def _fetch_existing_numbers(prefix_code: str) -> list[str]:
    pattern = f"{prefix_code}-KN-{jalali_year()}-%"
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT kn_number FROM knowledge_entries WHERE kn_number LIKE ?",
            (pattern,),
        ).fetchall()
    return [r["kn_number"] for r in rows]


def generate_knowledge_number(project_id: int | None) -> str:
    """
    شمارهٔ یکتای بعدی دانش را برای یک پروژه (یا عمومی اگر None) تولید میکند.

    فقط یک بار و قبل از submit صدا زده میشود؛ بعد از submit، kn_number در
    دیتابیس ذخیره و همان شماره در پیشنویس نهایی نوشته میشود.
    """
    code = project_code(project_id)
    serial = max_serial_for_project(project_id) + 1
    return f"{code}-KN-{jalali_year()}-{serial:03d}"
