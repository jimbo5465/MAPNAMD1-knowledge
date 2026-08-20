"""
هندلر مشاهدات صحرایی (observations).
ثبت سریع یک مشاهده بدون قالب، مرور، افزودن، ارتقا به دانش.
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

import config
from db.models import (
    add_observation,
    archive_observation,
    get_observation_by_id,
    list_observations_by_user,
    promote_observation,
    update_observation,
)
from handlers.auth import require_registration
from handlers.keyboards import back_to_main_keyboard, main_menu_keyboard
from handlers.knowledge import _transcribe_voice

logger = logging.getLogger(__name__)

# States
OBS_CONTENT, OBS_EXTEND = range(2)


def _status_label(status: str) -> str:
    labels = {
        "raw": "🟡 خام",
        "maturing": "🟠 در حال بررسی",
        "promoted": "🟢 تبدیل به دانش",
        "archived": "⚪ بایگانی",
    }
    return labels.get(status, status)


def _obs_list_keyboard(obs_list: list[dict]) -> InlineKeyboardMarkup:
    """دکمه‌های هر مشاهده برای انتخاب."""
    rows = []
    for obs in obs_list[:10]:
        snippet = (obs.get("content") or "")[:40]
        rows.append([
            InlineKeyboardButton(
                f"#{obs['id']} — {snippet}",
                callback_data=f"obs:view:{obs['id']}",
            )
        ])
    rows.append([InlineKeyboardButton("➕ مشاهده جدید", callback_data="obs:new")])
    rows.append([InlineKeyboardButton("🏠 بازگشت به منو", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def _obs_view_keyboard(obs: dict) -> InlineKeyboardMarkup:
    """دکمه‌های عملیات روی یک مشاهده."""
    rows = []
    if obs.get("status") == "raw":
        rows.append([InlineKeyboardButton("✏️ افزودن مطلب", callback_data=f"obs:extend:{obs['id']}")])
        rows.append([InlineKeyboardButton("📝 ارتقا به دانش", callback_data=f"obs:promote:{obs['id']}")])
        rows.append([InlineKeyboardButton("🗑 بایگانی", callback_data=f"obs:archive:{obs['id']}")])
    elif obs.get("status") == "maturing":
        rows.append([InlineKeyboardButton("✏️ افزودن مطلب", callback_data=f"obs:extend:{obs['id']}")])
        rows.append([InlineKeyboardButton("📝 ارتقا به دانش", callback_data=f"obs:promote:{obs['id']}")])
        rows.append([InlineKeyboardButton("🗑 بایگانی", callback_data=f"obs:archive:{obs['id']}")])
    elif obs.get("status") == "promoted":
        rows.append([InlineKeyboardButton("🔗 مشاهده دانش", callback_data=f"kn:view:{obs['promoted_to_kn_id']}")])
    rows.append([InlineKeyboardButton("🏠 بازگشت به منو", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


@require_registration
async def obs_new_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع ثبت مشاهده جدید."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📓 *ثبت مشاهده جدید*\n\n"
        "مشاهده‌ی خود را بنویسید یا ویس بفرستید.\n"
        "🎙️ *راهنما:* فارسی صحبت کنید — صدای شما توسط هوش مصنوعی به متن تبدیل می‌شود.\n\n"
        "این یک یادداشت سریع و بدون قالب است — می‌توانید بعداً به آن مطلب اضافه کنید "
        "یا به دانش تبدیلش کنید.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 انصراف", callback_data="menu:main")],
        ]),
    )
    return OBS_CONTENT


@require_registration
async def obs_content_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت متن/ویس مشاهده."""
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("❌ متن خالی است. دوباره بنویسید یا ویس بفرستید:")
        return OBS_CONTENT

    user = update.effective_user
    if not user:
        return ConversationHandler.END

    try:
        obs_id = add_observation(
            telegram_id=user.id,
            content=text,
            project_name=None,
            tags=[],
        )
        logger.info("مشاهده جدید ثبت شد: #%d توسط %d", obs_id, user.id)
    except Exception:
        logger.exception("خطا در ثبت مشاهده")
        await update.message.reply_text("❌ خطا در ثبت مشاهده. دوباره تلاش کنید.")
        return OBS_CONTENT

    await update.message.reply_text(
        f"✅ *مشاهده ثبت شد* (#{obs_id})\n\n"
        f"_{text[:200]}_\n\n"
        "می‌توانید همین حالا مطلب بیشتری اضافه کنید، یا بعداً از «مشاهده‌های من» به آن برگردید.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ افزودن مطلب", callback_data=f"obs:extend:{obs_id}")],
            [InlineKeyboardButton("📝 ارتقا به دانش", callback_data=f"obs:promote:{obs_id}")],
            [InlineKeyboardButton("🏠 بازگشت به منو", callback_data="menu:main")],
        ]),
    )
    return ConversationHandler.END


@require_registration
async def obs_voice_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت ویس برای مشاهده."""
    text = await _transcribe_voice(update, context)
    if text is None:
        return OBS_CONTENT
    # شبیه‌سازی پیام متنی
    original = update.message.text
    update.message.text = text
    try:
        return await obs_content_received(update, context)
    finally:
        update.message.text = original


@require_registration
async def obs_list_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """نمایش لیست مشاهدات کاربر."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    obs_list = list_observations_by_user(user.id)
    if not obs_list:
        await query.edit_message_text(
            "📭 *هیچ مشاهده‌ای ثبت نکرده‌اید.*\n\n"
            "با «📓 ثبت مشاهده» می‌توانید اولین مشاهده را ثبت کنید.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ ثبت اولین مشاهده", callback_data="obs:new")],
                [InlineKeyboardButton("🏠 بازگشت به منو", callback_data="menu:main")],
            ]),
        )
        return ConversationHandler.END

    lines = [f"📂 *مشاهدات شما* ({len(obs_list)} مورد):\n"]
    for obs in obs_list[:10]:
        snippet = (obs.get("content") or "")[:60].replace("\n", " ")
        lines.append(f"• #{obs['id']} — {snippet}...\n   {_status_label(obs['status'])}")

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=_obs_list_keyboard(obs_list),
    )
    return ConversationHandler.END


@require_registration
async def obs_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """نمایش جزئیات یک مشاهده."""
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    obs_id = int(data.split(":")[2])

    obs = get_observation_by_id(obs_id)
    if not obs:
        await query.edit_message_text("⚠️ مشاهده یافت نشد.", reply_markup=back_to_main_keyboard())
        return ConversationHandler.END

    from datetime import datetime
    created = obs.get("created_at", "")
    try:
        created_fa = created[:16].replace("T", " ")
    except Exception:
        created_fa = created

    text = (
        f"📓 *مشاهده #{obs_id}*\n"
        f"وضعیت: {_status_label(obs['status'])}\n"
        f"زمان: {created_fa}\n\n"
        f"{obs.get('content')}"
    )
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=_obs_view_keyboard(obs))
    return ConversationHandler.END


@require_registration
async def obs_extend_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع افزودن مطلب به مشاهده."""
    query = update.callback_query
    await query.answer()
    obs_id = int((query.data or "").split(":")[2])
    context.user_data["obs_extend_id"] = obs_id

    obs = get_observation_by_id(obs_id)
    if not obs:
        await query.edit_message_text("⚠️ مشاهده یافت نشد.", reply_markup=back_to_main_keyboard())
        return ConversationHandler.END

    # تغییر وضعیت به maturing
    from db.models import update_observation as _upd
    if obs["status"] == "raw":
        _upd(obs_id, content=obs["content"])  # فقط برای touch (updated_at)

    await query.edit_message_text(
        f"✏️ *افزودن مطلب به مشاهده #{obs_id}*\n\n"
        "مطلب جدید را بنویسید یا ویس بفرستید. به محتوای قبلی اضافه می‌شود.\n\n"
        f"محتوای فعلی:\n_{obs.get('content')[:300]}_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تمام شد", callback_data="menu:main")],
        ]),
    )
    return OBS_EXTEND


@require_registration
async def obs_extend_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت مطلب جدید برای افزودن به مشاهده."""
    obs_id = context.user_data.get("obs_extend_id")
    if not obs_id:
        return ConversationHandler.END

    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("❌ متن خالی است. دوباره بنویسید:")
        return OBS_EXTEND

    obs = get_observation_by_id(obs_id)
    if not obs:
        await update.message.reply_text("⚠️ مشاهده یافت نشد.")
        return ConversationHandler.END

    new_content = obs.get("content", "") + "\n\n" + text
    try:
        update_observation(obs_id, content=new_content)
    except Exception:
        logger.exception("خطا در افزودن مطلب به مشاهده %d", obs_id)
        await update.message.reply_text("❌ خطا در ذخیره. دوباره تلاش کنید.")
        return OBS_EXTEND

    await update.message.reply_text(
        f"✅ مطلب به مشاهده #{obs_id} اضافه شد.\n\n"
        "می‌توانید ادامه دهید یا تمام کنید.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 ارتقا به دانش", callback_data=f"obs:promote:{obs_id}")],
            [InlineKeyboardButton("🏠 بازگشت به منو", callback_data="menu:main")],
        ]),
    )
    return OBS_EXTEND


@require_registration
async def obs_promote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """ارتقای مشاهده به دانش — وصل به فلوی ثبت دانش."""
    query = update.callback_query
    await query.answer()
    obs_id = int((query.data or "").split(":")[2])

    obs = get_observation_by_id(obs_id)
    if not obs:
        await query.edit_message_text("⚠️ مشاهده یافت نشد.", reply_markup=back_to_main_keyboard())
        return ConversationHandler.END

    # ذخیره در user_data تا handler دانش بتواند از آن استفاده کند
    context.user_data["kn_observation_pending"] = {
        "obs_id": obs_id,
        "content": obs.get("content", ""),
    }

    # هدایت به انتخاب نوع دانش (مثل kn_type ولی با پر کردن description از مشاهده)
    from handlers.knowledge import kn_mode_entry
    await query.edit_message_text(
        "📝 *ارتقا مشاهده به دانش*\n\n"
        "مشاهدهٔ شما به عنوان «شرح اولیه» در نظر گرفته می‌شود.\n"
        "ابتدا روش ثبت را انتخاب کنید:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✍️ ثبت دستی", callback_data="kn:promote:manual")],
            [InlineKeyboardButton("🎙️ مصاحبه با AI", callback_data="kn:promote:interview")],
        ]),
    )
    return ConversationHandler.END


@require_registration
async def obs_archive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """بایگانی مشاهده."""
    query = update.callback_query
    await query.answer()
    obs_id = int((query.data or "").split(":")[2])
    try:
        archive_observation(obs_id)
    except Exception:
        logger.exception("خطا در بایگانی مشاهده %d", obs_id)
        await query.edit_message_text("❌ خطا در بایگانی.")
        return ConversationHandler.END

    await query.edit_message_text(
        f"🗑 مشاهده #{obs_id} بایگانی شد.",
        reply_markup=back_to_main_keyboard(),
    )
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# فیلتر صوتی (مشترک با knowledge.py)
# ══════════════════════════════════════════════════════════════════════════════

class _AudioMessageFilter(filters.MessageFilter):
    def filter(self, message):
        if not message:
            return False
        if message.voice or message.audio or message.video_note:
            return True
        if message.document and message.document.mime_type:
            return message.document.mime_type.startswith("audio")
        return False


AUDIO_MESSAGE_FILTER = _AudioMessageFilter()


# ══════════════════════════════════════════════════════════════════════════════
# ساخت ConversationHandler
# ══════════════════════════════════════════════════════════════════════════════

def get_observations_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(obs_new_start, pattern=r"^obs:new$"),
            CallbackQueryHandler(obs_list_start, pattern=r"^obs:list$"),
            CallbackQueryHandler(obs_view, pattern=r"^obs:view:\d+$"),
            CallbackQueryHandler(obs_extend_start, pattern=r"^obs:extend:\d+$"),
            CallbackQueryHandler(obs_promote, pattern=r"^obs:promote:\d+$"),
            CallbackQueryHandler(obs_archive, pattern=r"^obs:archive:\d+$"),
        ],
        states={
            OBS_CONTENT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, obs_content_received),
                MessageHandler(AUDIO_MESSAGE_FILTER, obs_voice_received),
            ],
            OBS_EXTEND: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, obs_extend_received),
                MessageHandler(AUDIO_MESSAGE_FILTER, obs_voice_received),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(obs_list_start, pattern=r"^obs:list$"),
        ],
        per_message=False,
        name="observations",
        persistent=False,
    )