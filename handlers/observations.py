"""
هندلر مشاهدات صحرایی (observations).
ثبت سریع یک مشاهده بدون قالب، مرور، افزودن، ارتقا به دانش.
پشتیبانی از: متن، ویس (با تأیید/اصلاح)، پیوست (عکس/PDF/فایل).
"""

from __future__ import annotations

import logging
import os

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
    add_observation_attachment,
    archive_observation,
    get_observation_by_id,
    list_observation_attachments,
    list_observations_by_user,
    update_observation,
)
from handlers.auth import require_registration
from handlers.keyboards import back_to_main_keyboard, main_menu_keyboard
from handlers.knowledge import _transcribe_voice
from utils.busy_lock import clear_busy, is_busy, set_busy

logger = logging.getLogger(__name__)

# States
(
    OBS_CONTENT,       # 0 — دریافت متن/ویس/عکس اولیه
    OBS_CONFIRM_VOICE, # 1 — تأیید/اصلاح متن ترنسکرایب‌شده
    OBS_EDIT_CHOICE,   # 2 — انتخاب جایگزین/افزودن برای اصلاح
    OBS_ATTACHMENTS,   # 3 — دریافت پیوست‌ها
    OBS_EXTEND,        # 4 — افزودن مطلب به مشاهده موجود
) = range(5)

_OBS_TMP_DIR = "/tmp/welderbot_obs_attachments"


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
    if obs.get("status") in ("raw", "maturing"):
        rows.append([InlineKeyboardButton("✏️ افزودن مطلب", callback_data=f"obs:extend:{obs['id']}")])
        rows.append([InlineKeyboardButton("📝 ارتقا به دانش", callback_data=f"obs:promote:{obs['id']}")])
        rows.append([InlineKeyboardButton("🗑 بایگانی", callback_data=f"obs:archive:{obs['id']}")])
    elif obs.get("status") == "promoted":
        rows.append([InlineKeyboardButton("🔗 مشاهده دانش", callback_data=f"kn:view:{obs['promoted_to_kn_id']}")])
    rows.append([InlineKeyboardButton("🏠 بازگشت به منو", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def _attachments_confirm_keyboard(obs_id: int) -> InlineKeyboardMarkup:
    """دکمه‌های بعد از ذخیره متن: پیوست یا تمام."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼️ افزودن عکس", callback_data=f"obs:add_photo:{obs_id}")],
        [InlineKeyboardButton("📎 افزودن فایل (PDF/...)", callback_data=f"obs:add_file:{obs_id}")],
        [InlineKeyboardButton("✅ تمام شد", callback_data="menu:main")],
    ])


def _voice_confirm_keyboard(obs_id: int | None) -> InlineKeyboardMarkup:
    """دکمه‌های تأیید/اصلاح متن ترنسکرایب‌شده."""
    buttons = [
        [InlineKeyboardButton("✏️ اصلاح متن", callback_data="obs:edit_voice")],
        [InlineKeyboardButton("✅ تأیید و ذخیره", callback_data="obs:confirm_voice")],
    ]
    if obs_id:
        buttons.append([InlineKeyboardButton("🏠 انصراف", callback_data="menu:main")])
    return InlineKeyboardMarkup(buttons)


def _edit_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔁 جایگزین متن قبلی شود", callback_data="obs:edit_replace")],
        [InlineKeyboardButton("➕ به متن قبلی اضافه شود", callback_data="obs:edit_append")],
    ])


# ══════════════════════════════════════════════════════════════════════════════
# شروع ثبت مشاهده
# ══════════════════════════════════════════════════════════════════════════════

@require_registration
async def obs_new_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع ثبت مشاهده جدید."""
    query = update.callback_query
    await query.answer()
    context.user_data["obs_new_photos"] = []
    context.user_data["obs_new_files"] = []
    await query.edit_message_text(
        "📓 *ثبت مشاهده جدید*\n\n"
        "مشاهده‌ی خود را بنویسید، ویس بفرستید، یا عکس/فایل ضمیمه کنید.\n"
        "🎙️ *راهنما:* فارسی صحبت کنید — صدای شما توسط هوش مصنوعی به متن تبدیل می‌شود.\n\n"
        "پس از ثبت، می‌توانید پیوست هم اضافه کنید.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 انصراف", callback_data="menu:main")],
        ]),
    )
    return OBS_CONTENT


# ══════════════════════════════════════════════════════════════════════════════
# دریافت متن
# ══════════════════════════════════════════════════════════════════════════════

@require_registration
async def obs_content_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت متن مشاهده."""
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("❌ متن خالی است. دوباره بنویسید یا ویس بفرستید:")
        return OBS_CONTENT
    return await _save_observation_text(update, context, text)


async def _save_observation_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> int:
    """ذخیره مشاهده در DB و نمایش پیام تأیید + گزینه پیوست."""
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

    context.user_data["obs_saved_id"] = obs_id

    await update.message.reply_text(
        f"✅ *مشاهده ثبت شد* (#{obs_id})\n\n"
        f"_{text[:200]}_\n\n"
        "آیا می‌خواهید ضمیمه‌ای (عکس، PDF، فایل) اضافه کنید؟",
        parse_mode="Markdown",
        reply_markup=_attachments_confirm_keyboard(obs_id),
    )
    return OBS_ATTACHMENTS


# ══════════════════════════════════════════════════════════════════════════════
# دریافت ویس + تأیید/اصلاح
# ══════════════════════════════════════════════════════════════════════════════

@require_registration
async def obs_voice_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت ویس برای مشاهده — متن ترنسکرایب‌شده را نشان می‌دهد و تأیید می‌گیرد."""
    user = update.effective_user
    if user and is_busy(user.id):
        await update.message.reply_text(
            "⏳ هوش مصنوعی در حال بررسی است... لطفاً کمی صبر کنید."
        )
        return OBS_CONTENT
    if user:
        set_busy(user.id)
    try:
        text = await _transcribe_voice(update, context)
    finally:
        if user:
            clear_busy(user.id)
    if text is None:
        return OBS_CONTENT

    context.user_data["obs_voice_text"] = text

    await update.message.reply_text(
        f"📝 *متن تشخیص‌داده‌شده از ویس شما:*\n\n_{text}_\n\n"
        "آیا این متن درست است؟ می‌توانید اصلاح کنید.",
        parse_mode="Markdown",
        reply_markup=_voice_confirm_keyboard(None),
    )
    return OBS_CONFIRM_VOICE


@require_registration
async def obs_confirm_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """تأیید متن ترنسکرایب‌شده ← ذخیره مستقیم."""
    query = update.callback_query
    await query.answer()
    text = context.user_data.get("obs_voice_text", "")
    if not text:
        await query.edit_message_text("❌ متنی یافت نشد. دوباره ویس بفرستید.")
        return OBS_CONTENT
    return await _save_observation_text(update, context, text)


@require_registration
async def obs_edit_voice_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع ویرایش متن ترنسکرایب‌شده."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✏️ متن اصلاح‌شده را بنویسید:",
    )
    return OBS_CONFIRM_VOICE  # next text message will hit obs_edit_voice_text


@require_registration
async def obs_edit_voice_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت متن اصلاح‌شده — سؤال جایگزین یا افزودن."""
    corrected = (update.message.text or "").strip()
    if not corrected:
        await update.message.reply_text("❌ متن خالی است. دوباره بنویسید:")
        return OBS_CONFIRM_VOICE

    context.user_data["obs_edited_text"] = corrected
    await update.message.reply_text(
        "🔄 این متن اصلاح‌شده:\n\n"
        f"_{corrected[:200]}_\n\n"
        "آیا می‌خواهید این متن جایگزین متن تشخیص‌داده‌شده شود یا به آن اضافه شود؟",
        parse_mode="Markdown",
        reply_markup=_edit_choice_keyboard(),
    )
    return OBS_EDIT_CHOICE


@require_registration
async def obs_edit_replace(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """جایگزینی متن تشخیص‌داده‌شده با متن اصلاحی."""
    query = update.callback_query
    await query.answer()
    corrected = context.user_data.get("obs_edited_text", "")
    if not corrected:
        await query.edit_message_text("❌ متنی برای ذخیره یافت نشد.")
        return OBS_CONTENT
    context.user_data["obs_voice_text"] = corrected
    return await _save_observation_text(update, context, corrected)


@require_registration
async def obs_edit_append(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """افزودن متن اصلاحی به تشخیص داده‌شده."""
    query = update.callback_query
    await query.answer()
    original = context.user_data.get("obs_voice_text", "")
    corrected = context.user_data.get("obs_edited_text", "")
    combined = original + "\n\n(اصلاح: " + corrected + ")"
    context.user_data["obs_voice_text"] = combined
    return await _save_observation_text(update, context, combined)


# ══════════════════════════════════════════════════════════════════════════════
# پیوست‌ها (عکس/PDF/فایل)
# ══════════════════════════════════════════════════════════════════════════════

@require_registration
async def obs_add_photo_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع افزودن عکس به مشاهده."""
    query = update.callback_query
    await query.answer()
    obs_id = int((query.data or "").split(":")[2])
    context.user_data["obs_attach_obs_id"] = obs_id
    await query.edit_message_text(
        "🖼️ عکس را ارسال کنید. پس از دریافت، می‌توانید ادامه دهید یا تمام کنید.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تمام شد", callback_data="menu:main")],
        ]),
    )
    return OBS_ATTACHMENTS


@require_registration
async def obs_add_file_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع افزودن فایل (PDF/...) به مشاهده."""
    query = update.callback_query
    await query.answer()
    obs_id = int((query.data or "").split(":")[2])
    context.user_data["obs_attach_obs_id"] = obs_id
    await query.edit_message_text(
        "📎 فایل (PDF، سند و...) را ارسال کنید. پس از دریافت، می‌توانید ادامه دهید یا تمام کنید.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تمام شد", callback_data="menu:main")],
        ]),
    )
    return OBS_ATTACHMENTS


@require_registration
async def obs_attachment_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت عکس/فایل برای پیوست به مشاهده."""
    obs_id = context.user_data.get("obs_attach_obs_id") or context.user_data.get("obs_saved_id")
    if not obs_id:
        await update.message.reply_text("❌ شناسه مشاهده یافت نشد. دوباره /start بزنید.")
        return OBS_ATTACHMENTS

    # تشخیص نوع فایل
    msg = update.message
    file_id = None
    file_name = None
    mime_type = None

    if msg.photo:
        # عکس — آخرین (بزرگترین) سایز
        file_id = msg.photo[-1].file_id
        file_name = f"photo_{file_id[:12]}.jpg"
        mime_type = "image/jpeg"
    elif msg.document:
        file_id = msg.document.file_id
        file_name = msg.document.file_name or f"file_{file_id[:12]}"
        mime_type = msg.document.mime_type or "application/octet-stream"

    if not file_id:
        await update.message.reply_text("❌ فقط عکس و فایل پشتیبانی می‌شود. دوباره بفرستید:")
        return OBS_ATTACHMENTS

    # دانلود فایل
    try:
        file = await context.bot.get_file(file_id)
        os.makedirs(_OBS_TMP_DIR, exist_ok=True)
        obs_dir = os.path.join(config.OBS_ATTACH_PATH, str(obs_id))
        os.makedirs(obs_dir, exist_ok=True)
        # جلوگیری از overwrite
        base, ext = os.path.splitext(file_name or "file")
        dest = os.path.join(obs_dir, file_name or f"attachment_{file_id[:12]}")
        counter = 1
        while os.path.exists(dest):
            dest = os.path.join(obs_dir, f"{base}_{counter}{ext}")
            counter += 1

        await file.download_to_drive(dest)
        file_size = os.path.getsize(dest)

        # ثبت در DB
        add_observation_attachment(
            observation_id=obs_id,
            file_path=dest,
            file_name=file_name or os.path.basename(dest),
            mime_type=mime_type,
            file_size=file_size,
        )
        logger.info("پیوست به مشاهده #%d افزوده شد: %s", obs_id, dest)
    except Exception:
        logger.exception("خطا در دانلود پیوست مشاهده")
        await update.message.reply_text("❌ خطا در دریافت پیوست. دوباره تلاش کنید.")
        return OBS_ATTACHMENTS

    type_label = "🖼️ عکس" if mime_type and mime_type.startswith("image") else "📎 فایل"
    await update.message.reply_text(
        f"✅ {type_label} ذخیره شد.\n\n"
        "می‌توانید پیوست دیگری اضافه کنید یا تمام کنید.",
        reply_markup=_attachments_confirm_keyboard(obs_id),
    )
    return OBS_ATTACHMENTS


# ══════════════════════════════════════════════════════════════════════════════
# لیست مشاهدات
# ══════════════════════════════════════════════════════════════════════════════

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
        att_count = len(list_observation_attachments(obs["id"]))
        att_label = f" 📎{att_count}" if att_count else ""
        lines.append(f"• #{obs['id']} — {snippet}...\n   {_status_label(obs['status'])}{att_label}")

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=_obs_list_keyboard(obs_list),
    )
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# مشاهده جزئیات
# ══════════════════════════════════════════════════════════════════════════════

@require_registration
async def obs_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """نمایش جزئیات یک مشاهده با پیوست‌ها."""
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    obs_id = int(data.split(":")[2])

    obs = get_observation_by_id(obs_id)
    if not obs:
        await query.edit_message_text("⚠️ مشاهده یافت نشد.", reply_markup=back_to_main_keyboard())
        return ConversationHandler.END

    created = obs.get("created_at", "")
    try:
        created_fa = created[:16].replace("T", " ")
    except Exception:
        created_fa = created

    # پیوست‌ها
    attachments = list_observation_attachments(obs_id)
    att_lines = []
    if attachments:
        att_lines.append("\n📎 *پیوست‌ها:*")
        for a in attachments:
            name = a.get("file_name") or "فایل"
            mime = a.get("mime_type") or ""
            icon = "🖼️" if mime.startswith("image") else "📄"
            size = a.get("file_size") or 0
            size_str = f" ({size // 1024}KB)" if size > 0 else ""
            att_lines.append(f"  {icon} {name}{size_str}")
    else:
        att_lines.append("\n_بدون پیوست_")

    text = (
        f"📓 *مشاهده #{obs_id}*\n"
        f"وضعیت: {_status_label(obs['status'])}\n"
        f"زمان: {created_fa}\n\n"
        f"{obs.get('content')}"
        + "\n".join(att_lines)
    )
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=_obs_view_keyboard(obs))
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# افزودن مطلب به مشاهده موجود
# ══════════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════════
# ارتقا به دانش / بایگانی
# ══════════════════════════════════════════════════════════════════════════════

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

    context.user_data["kn_observation_pending"] = {
        "obs_id": obs_id,
        "content": obs.get("content", ""),
    }

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


class _PhotoDocFilter(filters.MessageFilter):
    """عکس یا فایل (document)."""
    def filter(self, message):
        if not message:
            return False
        if message.photo:
            return True
        if message.document:
            return True
        return False


PHOTO_DOC_FILTER = _PhotoDocFilter()


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
                MessageHandler(PHOTO_DOC_FILTER, obs_attachment_received),
            ],
            OBS_CONFIRM_VOICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, obs_edit_voice_text),
                CallbackQueryHandler(obs_confirm_voice, pattern=r"^obs:confirm_voice$"),
                CallbackQueryHandler(obs_edit_voice_start, pattern=r"^obs:edit_voice$"),
            ],
            OBS_EDIT_CHOICE: [
                CallbackQueryHandler(obs_edit_replace, pattern=r"^obs:edit_replace$"),
                CallbackQueryHandler(obs_edit_append, pattern=r"^obs:edit_append$"),
            ],
            OBS_ATTACHMENTS: [
                MessageHandler(filters.PHOTO, obs_attachment_received),
                MessageHandler(filters.Document.ALL, obs_attachment_received),
                CallbackQueryHandler(obs_add_photo_start, pattern=r"^obs:add_photo:\d+$"),
                CallbackQueryHandler(obs_add_file_start, pattern=r"^obs:add_file:\d+$"),
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