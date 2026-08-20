"""
نقطه ورود MAPNAMD1-knowledge — ربات دانش سازمانی مپنا توسعه یک.
کارها:
  - راه‌اندازی logging
  - مقداردهی اولیه DB
  - ساخت Application
  - ثبت تمام handlers
  - شروع polling
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

# ── ۱. پیکربندی logging ──────────────────────────────────────────────────────
_LOG_DIR = Path("logs")
_LOG_DIR.mkdir(exist_ok=True)

_log_level = logging.DEBUG if os.environ.get("KNOWLEDGEBOT_DEBUG") == "1" else logging.INFO

_fmt = logging.Formatter(
    fmt="%(asctime)s | %(name)-25s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_stdout_handler = logging.StreamHandler(sys.stdout)
_stdout_handler.setFormatter(_fmt)

_file_handler = logging.handlers.RotatingFileHandler(
    filename=_LOG_DIR / "knowledgebot.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
_file_handler.setFormatter(_fmt)

logging.basicConfig(
    level=_log_level,
    handlers=[_stdout_handler, _file_handler],
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# ── ۲. import‌های پروژه ──────────────────────────────────────────────────────
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

import config
from db.init import init_db
from handlers.knowledge import get_knowledge_conversation_handler
from handlers.registration import get_registration_conversation_handler
from handlers.observations import get_observations_conversation_handler


# ══════════════════════════════════════════════════════════════════════════════
# هندلر سراسری خطا
# ══════════════════════════════════════════════════════════════════════════════

async def global_error_handler(update: object, context) -> None:
    logger.exception(
        "خطای پردازش‌نشده | update=%s | خطا=%s",
        type(update).__name__,
        context.error,
        exc_info=context.error,
    )
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "❌ خطایی رخ داد، لطفاً دوباره تلاش کنید.\n"
                "اگر مشکل ادامه داشت /start را بزنید."
            )
    except Exception:
        logger.exception("خطا در ارسال پیام خطا به کاربر")


# ══════════════════════════════════════════════════════════════════════════════
# هندلر fallback برای ورودی‌های ناشناخته
# ══════════════════════════════════════════════════════════════════════════════

async def unknown_message_handler(update: Update, context) -> None:
    await update.message.reply_text(
        "❓ این دستور شناخته نشد.\n"
        "برای شروع /start را بزنید."
    )


async def unknown_callback_handler(update: Update, context) -> None:
    if update.callback_query:
        await update.callback_query.answer(
            "⚠️ این دکمه دیگر معتبر نیست. لطفاً /start را بزنید.",
            show_alert=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# منوی اصلی
# ══════════════════════════════════════════════════════════════════════════════

async def menu_main_handler(update: Update, context) -> None:
    """بازگشت به منوی اصلی."""
    from handlers.auth import is_registered
    from handlers.keyboards import main_menu_keyboard

    user = update.effective_user
    if not user or not is_registered(user.id):
        from handlers.registration import start_registration
        await start_registration(update, context)
        return

    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text(
            "🏠 *منوی اصلی* — ربات دانش سازمانی مپنا توسعه یک\n\n"
            "از گزینه‌های زیر انتخاب کنید:",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(user.id),
        )
    else:
        await update.message.reply_text(
            "🏠 *منوی اصلی* — ربات دانش سازمانی مپنا توسعه یک",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(user.id),
        )


async def help_command(update: Update, context) -> None:
    await update.message.reply_text(
        "📖 *راهنمای ربات دانش*\n\n"
        "/start — شروع و منوی اصلی\n"
        "/cancel — لغو عملیات جاری\n"
        "/help — نمایش این راهنما\n\n"
        "گزینه‌ها:\n"
        "📓 ثبت مشاهده — ثبت سریع یک مشاهده صحرایی\n"
        "📝 ثبت دانش — ثبت دانش/تجربه با قالب DANA\n"
        "🗂️ مشاهده‌های من — مرور مشاهدات ثبت‌شده\n"
        "👤 پروفایل من — مشاهده و ویرایش اطلاعات",
        parse_mode="Markdown",
    )


# ══════════════════════════════════════════════════════════════════════════════
# تابع اصلی
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    logger.info("=" * 60)
    logger.info("در حال راه‌اندازی ربات MAPNAMD1-knowledge ...")
    logger.info("سطح لاگ: %s", logging.getLevelName(_log_level))
    logger.info("مسیر DB: %s", config.DB_PATH)
    logger.info("=" * 60)

    if not config.BOT_TOKEN or config.BOT_TOKEN == "PLACEHOLDER_BOT_TOKEN":
        print(
            "\n❌ خطا: متغیر محیطی BOT_TOKEN تنظیم نشده است.\n"
            "   لطفاً قبل از اجرا این دستور را بزنید:\n"
            "   export BOT_TOKEN='توکن_ربات_شما'\n",
            file=sys.stderr,
        )
        sys.exit(1)

    logger.info("در حال مقداردهی اولیه پایگاه داده ...")
    try:
        init_db()
        logger.info("✅ پایگاه داده آماده است.")
    except Exception as exc:
        logger.critical(
            "❌ خطای کشنده در راه‌اندازی پایگاه داده: %s\n"
            "ربات بدون DB اجرا نمی‌شود. مسیر: %s",
            exc,
            config.DB_PATH,
            exc_info=True,
        )
        sys.exit(2)

    logger.info("در حال ساخت Application تلگرام ...")
    app = Application.builder().token(config.BOT_TOKEN).build()

    logger.info("در حال ثبت handlers ...")

    app.add_error_handler(global_error_handler)

    # ثبت‌نام کاربر (اولویت بالا — قبل از همه)
    app.add_handler(get_registration_conversation_handler())
    logger.info("  ✓ ConversationHandler ثبت‌نام کاربر ثبت شد")

    # ثبت مشاهده
    app.add_handler(get_observations_conversation_handler())
    logger.info("  ✓ ConversationHandler ثبت مشاهده ثبت شد")

    # ثبت دانش/تجربه سازمانی
    app.add_handler(get_knowledge_conversation_handler())
    logger.info("  ✓ ConversationHandler ثبت دانش/تجربه ثبت شد")

    # منوی اصلی
    app.add_handler(CallbackQueryHandler(menu_main_handler, pattern=r"^menu:main$"))
    app.add_handler(CommandHandler("help", help_command))
    logger.info("  ✓ CommandHandlers ثبت شدند (/start /cancel /help /menu)")

    # fallback
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            unknown_message_handler,
        )
    )
    app.add_handler(
        CallbackQueryHandler(unknown_callback_handler)
    )
    logger.info("  ✓ Fallback handlers ثبت شدند")

    logger.info("✅ تمام handlers ثبت شدند.")

    logger.info("🚀 MAPNAMD1-knowledge در حال اجرا است. منتظر پیام‌ها...")
    logger.info("برای توقف Ctrl+C را بزنید.")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()