"""
هندلر ثبت‌نام کاربر (معرفی).
کاربر با /start وارد می‌شود. اگر ثبت‌نام نکرده باشد، فرم پرسش اطلاعات را می‌بیند:
  - نام و نام خانوادگی
  - شماره تماس
  - کد پرسنلی
  - مشخصات پروژه (متن آزاد)
  - سمت
تا همه فیلدها تکمیل نشود، منو باز نمی‌شود.
"""

from __future__ import annotations

import logging

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from db.models import get_user_by_telegram_id, register_or_link_user, update_user
from handlers.keyboards import main_menu_keyboard

logger = logging.getLogger(__name__)

# States
NAME, PHONE, PERSONNEL_CODE, PROJECT, POSITION = range(5)


def _edit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ ویرایش اطلاعات", callback_data="reg:edit")],
    ])


async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """نقطه ورود — /start یا منوی اصلی."""
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    existing = get_user_by_telegram_id(user.id)
    if existing:
        # کاربر قبلاً ثبت‌نام کرده — منوی اصلی
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                "🏠 *منوی اصلی* — ربات دانش سازمانی مپنا توسعه یک",
                parse_mode="Markdown",
                reply_markup=main_menu_keyboard(user.id),
            )
        else:
            await update.message.reply_text(
                f"👋 خوش آمدید {existing.get('full_name') or user.first_name}!",
                reply_markup=main_menu_keyboard(user.id),
            )
        return ConversationHandler.END

    # کاربر جدید — شروع ثبت‌نام
    if update.callback_query:
        await update.callback_query.answer()
        msg = update.callback_query.message
    else:
        msg = update.message

    await msg.reply_text(
        "📋 *ثبت‌نام — معرفی خود*\n\n"
        "برای استفاده از ربات، لطفاً اطلاعات زیر را وارد کنید.\n\n"
        "۱️⃣ *نام و نام خانوادگی:*",
        parse_mode="Markdown",
    )
    return NAME


async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if not text or len(text) < 3:
        await update.message.reply_text("❌ نام باید حداقل ۳ حرف باشد. دوباره وارد کنید:")
        return NAME
    context.user_data["reg_name"] = text
    await update.message.reply_text(
        "✅ ثبت شد.\n\n"
        "۲️⃣ *شماره تماس:* (مثال: ۰۹۱۲۱۲۳۴۵۶۷)\n"
        "می‌توانید از دکمهٔ زیر استفاده کنید یا دستی تایپ کنید.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("📱 ارسال شماره تماس", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )
    return PHONE


async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # پشتیبانی از دکمه ارسال شماره تماس
    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        phone = (update.message.text or "").strip()
    if not phone:
        await update.message.reply_text("❌ شماره معتبر نیست. دوباره وارد کنید:")
        return PHONE
    context.user_data["reg_phone"] = phone
    await update.message.reply_text(
        "✅ ثبت شد.\n\n"
        "۳️⃣ *کد پرسنلی:*",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return PERSONNEL_CODE


async def get_personnel_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("❌ کد پرسنلی نمی‌تواند خالی باشد. دوباره وارد کنید:")
        return PERSONNEL_CODE
    context.user_data["reg_personnel_code"] = text
    await update.message.reply_text(
        "✅ ثبت شد.\n\n"
        "۴️⃣ *مشخصات پروژه:* (نام پروژه‌ای که در آن مشغول هستید)\n"
        "می‌توانید خالی بگذارید و بعداً ویرایش کنید.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭️ رد کردن این مرحله", callback_data="reg:skip_project")],
        ]),
    )
    return PROJECT


async def get_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    context.user_data["reg_project"] = text if text else None
    await _ask_position(update, context)
    return POSITION


async def skip_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["reg_project"] = None
    await _ask_position(update, context)
    return POSITION


async def _ask_position(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """پرسش سمت."""
    text = (
        "✅ ثبت شد.\n\n"
        "۵️⃣ *سمت:* (مثال: کارشناس تعمیرات، ناظر جوش، سرپرست واحد...)\n"
        "می‌توانید خالی بگذارید و بعداً ویرایش کنید."
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏭️ رد کردن این مرحله", callback_data="reg:skip_position")],
            ]),
        )
    else:
        await update.message.reply_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏭️ رد کردن این مرحله", callback_data="reg:skip_position")],
            ]),
        )


async def get_position(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    context.user_data["reg_position"] = text if text else None
    await _finish_registration(update, context)
    return ConversationHandler.END


async def skip_position(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["reg_position"] = None
    await _finish_registration(update, context)
    return ConversationHandler.END


async def _finish_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ذخیره اطلاعات در DB و نمایش منوی اصلی."""
    user = update.effective_user
    if not user:
        return

    linked = False
    try:
        # ثبت‌نام جدید یا اتصال به حساب بلهٔ همان شخص
        # (تطبیق: شمارهٔ نرمال‌شده + کد پرسنلی — لایهٔ امنیتی)
        _, linked = register_or_link_user(
            platform="telegram",
            platform_id=user.id,
            full_name=context.user_data.get("reg_name", user.full_name or "کاربر"),
            phone=context.user_data.get("reg_phone"),
            personnel_code=context.user_data.get("reg_personnel_code"),
            project_name=context.user_data.get("reg_project"),
            position=context.user_data.get("reg_position"),
        )
        if linked:
            logger.info("حساب تلگرام به حساب موجود متصل شد: %d", user.id)
        else:
            logger.info("کاربر جدید ثبت شد: %d (%s)", user.id, context.user_data.get("reg_name"))
    except Exception:
        logger.exception("خطا در ثبت کاربر: %d", user.id)
        linked = False

    # پاکسازی داده‌های موقت
    for key in list(context.user_data):
        if key.startswith("reg_"):
            context.user_data.pop(key, None)

    text = (
        "✅ *ثبت‌نام با موفقیت انجام شد!*\n\n"
        "به ربات دانش سازمانی مپنا توسعه یک خوش آمدید.\n"
        "از منوی زیر می‌توانید استفاده کنید:"
    )
    if linked:
        text += "\n\n🔗 حساب تلگرام شما به حساب بله‌تان متصل شد — دانش‌ها و مشاهدات شما در هر دو پلتفرم مشترک است."
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=main_menu_keyboard(user.id),
        )
    else:
        await update.message.reply_text(
            text, parse_mode="Markdown",
            reply_markup=main_menu_keyboard(user.id),
        )


# ══════════════════════════════════════════════════════════════════════════════
# منوی پروفایل
# ══════════════════════════════════════════════════════════════════════════════

async def profile_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """نمایش پروفایل کاربر."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    profile = get_user_by_telegram_id(user.id)
    if not profile:
        await query.edit_message_text("⚠️ پروفایل یافت نشد.")
        return ConversationHandler.END

    from handlers.keyboards import profile_keyboard
    text = (
        "👤 *پروفایل شما*\n\n"
        f"📛 نام: {profile['full_name']}\n"
        f"📞 شماره: {profile.get('phone') or '—'}\n"
        f"🆔 کد پرسنلی: {profile.get('personnel_code') or '—'}\n"
        f"🏗️ پروژه: {profile.get('project_name') or '—'}\n"
        f"💼 سمت: {profile.get('position') or '—'}"
    )
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=profile_keyboard())
    return ConversationHandler.END


async def profile_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع ویرایش پروفایل."""
    query = update.callback_query
    await query.answer()
    context.user_data["reg_name"] = None  # نشانه‌گذاری برای ویرایش
    await query.edit_message_text(
        "✏️ *ویرایش پروفایل*\n\n"
        "۱️⃣ *نام و نام خانوادگی:* (برای رد کردن، /skip را بزنید)",
        parse_mode="Markdown",
    )
    return NAME


async def profile_edit_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """رد کردن یک مرحله در ویرایش."""
    # مرحله فعلی را رد کن و برو به بعدی
    state = context.user_data.get("_edit_state", 0)
    if state == 0:  # اسم
        await update.message.reply_text("↩️ نام تغییری نکرد.")
        context.user_data["_edit_state"] = 1
        await update.message.reply_text(
            "۲️⃣ *شماره تماس:* (برای رد کردن، /skip را بزنید)",
            parse_mode="Markdown",
        )
        return PHONE
    elif state == 1:  # شماره
        await update.message.reply_text("↩️ شماره تغییری نکرد.")
        context.user_data["_edit_state"] = 2
        await update.message.reply_text(
            "۳️⃣ *کد پرسنلی:* (برای رد کردن، /skip را بزنید)",
            parse_mode="Markdown",
        )
        return PERSONNEL_CODE
    # ... ادامه
    await _finish_edit(update, context)
    return ConversationHandler.END


async def _finish_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """پایان ویرایش — ذخیره و بازگشت."""
    user = update.effective_user
    if not user:
        return
    # جمع‌آوری فیلدهای تغییر کرده
    updates = {}
    if context.user_data.get("reg_name"):
        updates["full_name"] = context.user_data["reg_name"]
    if context.user_data.get("reg_phone"):
        updates["phone"] = context.user_data["reg_phone"]
    if context.user_data.get("reg_personnel_code"):
        updates["personnel_code"] = context.user_data["reg_personnel_code"]
    if context.user_data.get("reg_project"):
        updates["project_name"] = context.user_data["reg_project"]
    if context.user_data.get("reg_position"):
        updates["position"] = context.user_data["reg_position"]

    if updates:
        update_user(user.id, **updates)

    # پاکسازی
    for key in list(context.user_data):
        if key.startswith("reg_") or key == "_edit_state":
            context.user_data.pop(key, None)

    from handlers.keyboards import main_menu_keyboard
    await update.message.reply_text(
        "✅ *پروفایل به‌روزرسانی شد.*",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(user.id),
    )


# ══════════════════════════════════════════════════════════════════════════════
# ساخت ConversationHandler
# ══════════════════════════════════════════════════════════════════════════════

def get_registration_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("start", start_registration),
            CallbackQueryHandler(profile_view, pattern=r"^profile:view$"),
            CallbackQueryHandler(profile_edit_start, pattern=r"^profile:edit$"),
        ],
        states={
            NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_name),
                CommandHandler("skip", profile_edit_skip),
            ],
            PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone),
                MessageHandler(filters.CONTACT, get_phone),
                CommandHandler("skip", profile_edit_skip),
            ],
            PERSONNEL_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_personnel_code),
                CommandHandler("skip", profile_edit_skip),
            ],
            PROJECT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_project),
                CallbackQueryHandler(skip_project, pattern=r"^reg:skip_project$"),
            ],
            POSITION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_position),
                CallbackQueryHandler(skip_position, pattern=r"^reg:skip_position$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", start_registration),
            CommandHandler("start", start_registration),
        ],
        per_message=False,
        name="user_registration",
        persistent=False,
    )