"""
پیکربندی سراسری MAPNAMD1-knowledge.
تمام مقادیر حساس از متغیرهای محیطی خوانده می‌شوند.
این ماژول هیچ import داخلی از پروژه ندارد.
"""

import os
import sys

# ─── توکن ربات تلگرام ────────────────────────────────────────────────────────

# توکن از متغیر محیطی BOT_TOKEN خوانده می‌شود
BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    print("❌ خطا: متغیر محیطی BOT_TOKEN تنظیم نشده است. ربات راه‌اندازی نمی‌شود.", file=sys.stderr)
    # در زمان import خطا نمی‌دهیم تا تست‌های db بدون token کار کنند؛
    # main.py این مقدار را بررسی و در صورت خالی بودن sys.exit می‌کند.

# ─── مسیرهای فایل‌سیستم ──────────────────────────────────────────────────────

# ریشه پروژه — پوشه‌ای که config.py در آن قرار دارد
_PROJECT_ROOT: str = os.path.dirname(os.path.abspath(__file__))

# مسیر فایل SQLite — کاملاً مستقل از welderbot.db
DB_PATH: str = os.path.join(_PROJECT_ROOT, "data", "knowledge.db")

# مسیر ذخیره عکس‌های ثبت دانش (هر رکورد یک زیرپوشه به نام knowledge id دارد)
KN_PHOTO_PATH: str = os.path.join(_PROJECT_ROOT, "media", "kn_photos")

# مسیر خروجی فایل‌های PDF/Word پیش‌نویس DANA ثبت دانش
KN_OUTPUT_PATH: str = os.path.join(_PROJECT_ROOT, "media", "exports", "kn")

# ─── هوش مصنوعی (استخراج فیلدهای دانش از متن آزاد) ──────────────────────────
# کلاینت سازگار با OpenAI (پروتکل /v1/chat/completions).
# پیش‌فرض: OpenCode Go — https://opencode.ai/zen/go/v1
# اگر کلید یا مدل تنظیم نشده باشد، AI غیرفعال است و ربات به حالت
# «پرسش دستی همه فیلدها» برمی‌گردد (fallback امن).

# پایگاه آدرس OpenAI-سازگار
KNOWLEDGE_AI_BASE_URL: str = os.environ.get(
    "KNOWLEDGE_AI_BASE_URL", "https://opencode.ai/zen/go/v1"
)

# کلید: اول KNOWLEDGE_AI_API_KEY، سپس OPENCODE_GO_API_KEY (کلید OpenCode Go)
KNOWLEDGE_AI_API_KEY: str = os.environ.get(
    "KNOWLEDGE_AI_API_KEY",
    os.environ.get("OPENCODE_GO_API_KEY", ""),
)

# نام مدل — باید دقیقاً با یکی از شناسه‌های /v1/models نقطه انتهایی یکی باشد.
# خالی = AI غیرفعال
KNOWLEDGE_AI_MODEL: str = os.environ.get("KNOWLEDGE_AI_MODEL", "")

# حداکثر زمان انتظار برای پاسخ مدل (ثانیه)
KNOWLEDGE_AI_TIMEOUT: float = float(os.environ.get("KNOWLEDGE_AI_TIMEOUT", "60"))

# ─── تبدیل گفتار به متن (STT) — Groq Whisper ─────────────────────────────────
# برای ثبت دانش/مشاهده: کاربر می‌تواند به‌جای تایپ، ویس بفرستد و ربات با
# whisper-large-v3-turbo روی Groq آن را به متن فارسی تبدیل می‌کند.
GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
GROQ_STT_MODEL: str = os.environ.get("GROQ_STT_MODEL", "whisper-large-v3-turbo")
GROQ_STT_BASE_URL: str = os.environ.get("GROQ_STT_BASE_URL", "https://api.groq.com/openai/v1")

# ─── ایجاد خودکار پوشه‌های runtime در زمان import ────────────────────────────

def _ensure_dirs() -> None:
    """پوشه‌های runtime لازم را در صورت نبود ایجاد می‌کند."""
    for _dir in (
        os.path.dirname(DB_PATH),   # data/
        KN_PHOTO_PATH,              # media/kn_photos/
        KN_OUTPUT_PATH,             # media/exports/kn/
    ):
        os.makedirs(_dir, exist_ok=True)

_ensure_dirs()
