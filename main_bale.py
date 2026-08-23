"""
نقطه ورود نسخه بله — ربات دانش سازمانی مپنا توسعه یک.
(معادل main.py نسخه تلگرام)

کارها:
  - راه‌اندازی logging
  - مقداردهی اولیه DB
  - ساخت Bot بله + Dispatcher
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
    filename=_LOG_DIR / "knowledgebot-bale.log",
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
logging.getLogger("bale").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# ── ۲. import‌های پروژه ──────────────────────────────────────────────────────
from bale import Bot, Update as BaleUpdate

import config
from db.init import init_db
from bale_app.framework import (
    CallbackQueryHandler,
    CommandHandler,
    Dispatcher,
    MessageHandler,
    filters,
)
from bale_app.knowledge import get_knowledge_conversation_handler
from bale_app.registration import get_registration_conversation_handler
from bale_app.observations import get_observations_conversation_handler
from utils.busy_lock import is_busy


# ══════════════════════════════════════════════════════════════════════════════
# هندلر «در حال پردازش» — وقتی AI مشغول است، کاربر پیام/دکمه بفرستد
# ══════════════════════════════════════════════════════════════════════════════

async def busy_guard_handler(update, context) -> None:
    """اگر کاربر در حال پردازش AI است، پیام کوتاه بده. در غیر این صورت هیچ کاری نکن."""
    user = update.effective_user
    if not user or not is_busy(user.id):
        return

    # پاسخ کوتاه — کاربر بداند ربات مشغول است
    try:
        if update.callback_query:
            await update.callback_query.answer(
                "⏳ هوش مصنوعی در حال بررسی است... لطفاً کمی صبر کنید.",
                show_alert=False,
            )
        elif update.message:
            await update.message.reply_text(
                "⏳ هوش مصنوعی در حال بررسی است... لطفاً کمی صبر کنید.",
            )
    except Exception:
        logger.exception("خطا در ارسال پیام busy")


# ══════════════════════════════════════════════════════════════════════════════
# هندلر سراسری خطا
# ══════════════════════════════════════════════════════════════════════════════

async def global_error_handler(update, context) -> None:
    logger.exception(
        "خطای پردازش‌نشده | update=%s | خطا=%s",
        type(update).__name__,
        context.error,
        exc_info=context.error,
    )
    try:
        if getattr(update, "effective_message", None):
            await update.effective_message.reply_text(
                "❌ خطایی رخ داد، لطفاً دوباره تلاش کنید.\n"
                "اگر مشکل ادامه داشت /start را بزنید."
            )
    except Exception:
        logger.exception("خطا در ارسال پیام خطا به کاربر")


# ══════════════════════════════════════════════════════════════════════════════
# هندلر fallback برای ورودی‌های ناشناخته
# ══════════════════════════════════════════════════════════════════════════════

async def unknown_message_handler(update, context) -> None:
    await update.message.reply_text(
        "❓ این دستور شناخته نشد.\n"
        "برای شروع /start را بزنید."
    )


async def unknown_callback_handler(update, context) -> None:
    if update.callback_query and update.callback_query.message:
        logger.warning(
            "callback ناشناخته دریافت شد: %r (کاربر %s)",
            update.callback_query.data,
            update.effective_user.id if update.effective_user else "?",
        )
        # در بله alert دکمه‌ای وجود ندارد — پیام مستقیم ارسال می‌شود
        await update.callback_query.message.reply_text(
            "⚠️ این دکمه دیگر معتبر نیست. لطفاً /start را بزنید.",
        )


# ══════════════════════════════════════════════════════════════════════════════
# منوی اصلی
# ══════════════════════════════════════════════════════════════════════════════

async def menu_main_handler(update, context) -> None:
    """بازگشت به منوی اصلی."""
    from bale_app.auth import is_registered
    from bale_app.keyboards import main_menu_keyboard
    from bale_app.framework import delete_tracked

    user = update.effective_user
    if not user or not is_registered(user.id):
        # فرم/پرامپت رهاشده حذف شود
        await delete_tracked(context)
        from bale_app.registration import start_registration
        await start_registration(update, context)
        return

    query = update.callback_query
    if query:
        await delete_tracked(context)
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


async def help_command(update, context) -> None:
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
# ساخت Dispatcher با همان ترتیب گروه‌بندی main.py تلگرام
# ══════════════════════════════════════════════════════════════════════════════

def build_dispatcher(bot: Bot) -> Dispatcher:
    dispatcher = Dispatcher(bot, busy_guard=busy_guard_handler)
    dispatcher.set_error_handler(global_error_handler)

    logger.info("در حال ثبت handlers ...")

    # ثبت‌نام کاربر (اولویت بالا — قبل از همه)
    dispatcher.add_conversation(get_registration_conversation_handler())
    logger.info("  ✓ ConversationHandler ثبت‌نام کاربر ثبت شد")

    # ثبت مشاهده
    dispatcher.add_conversation(get_observations_conversation_handler())
    logger.info("  ✓ ConversationHandler ثبت مشاهده ثبت شد")

    # ثبت دانش/تجربه سازمانی
    dispatcher.add_conversation(get_knowledge_conversation_handler())
    logger.info("  ✓ ConversationHandler ثبت دانش/تجربه ثبت شد")

    # منوی اصلی
    dispatcher.add_standalone(CallbackQueryHandler(menu_main_handler, pattern=r"^menu:main$"))
    dispatcher.add_standalone(CommandHandler("help", help_command))
    logger.info("  ✓ Standalone handlers ثبت شدند (menu:main / /help)")

    # fallback
    dispatcher.add_unknown_fallback(
        MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_message_handler)
    )
    dispatcher.add_unknown_fallback(CallbackQueryHandler(unknown_callback_handler))
    logger.info("  ✓ Fallback handlers ثبت شدند")

    logger.info("✅ تمام handlers ثبت شدند.")
    return dispatcher


# ══════════════════════════════════════════════════════════════════════════════
# تابع اصلی
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    logger.info("=" * 60)
    logger.info("در حال راه‌اندازی ربات MAPNAMD1-knowledge (نسخه بله) ...")
    logger.info("سطح لاگ: %s", logging.getLevelName(_log_level))
    logger.info("مسیر DB: %s", config.DB_PATH)
    logger.info("=" * 60)

    # توکن بله: ابتدا BALE_BOT_TOKEN (برای اجرای همزمان دو ربات)، سپس BOT_TOKEN
    # (.strip برای جلوگیری از فاصله/خط انتهایی هنگام set کردن در cmd)
    bale_token = (
        os.environ.get("BALE_BOT_TOKEN") or os.environ.get("BOT_TOKEN") or ""
    ).strip()
    if not bale_token:
        print(
            "\n❌ خطا: متغیر محیطی BALE_BOT_TOKEN تنظیم نشده است.\n"
            "   توکن ربات بله از BotFather داخل بله (ble.ir/BotFather):\n"
            "   set BALE_BOT_TOKEN=توکن_ربات_بله\n",
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

    logger.info("در حال ساخت Bot بله ...")
    bot = Bot(token=bale_token)

    dispatcher = build_dispatcher(bot)

    # مسیردهی مرکزی — معادل گروه‌بندی PTB در main.py تلگرام
    @bot.listen("on_ready")
    async def on_ready_handler():
        logger.info("🚀 MAPNAMD1-knowledge (Bale) آماده است: %s", bot.user)
        logger.info("برای توقف Ctrl+C را بزنید.")

    @bot.listen("on_update")
    async def on_update_handler(raw_update: BaleUpdate):
        try:
            await dispatcher.dispatch(raw_update)
        except Exception:
            # dispatch خودش error_handler دارد؛ این فقط محافظ اضافه است.
            logger.exception("خطا در dispatch")

    logger.info("🚀 MAPNAMD1-knowledge (Bale) در حال اجرا است. منتظر پیام‌ها...")
    bot.run()


if __name__ == "__main__":
    main()
