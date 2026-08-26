"""
هندلر مشاهدات صحرایی (observations) — نسخه بله.
ثبت سریع یک مشاهده بدون قالب، مرور، افزودن، ارتقا به دانش.
پشتیبانی از: متن، ویس (با تأیید/اصلاح)، پیوست (عکس/PDF/فایل).
(پورت از handlers/observations.py — فقط منبع import تغییر کرده)
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile

import config
from db.models import (
    add_observation,
    add_observation_attachment,
    archive_observation,
    get_observation_attachment,
    get_observation_by_id,
    get_user_by_telegram_id,
    list_observation_attachments,
    list_observations_by_user,
    search_observations,
    update_observation,
)
from bale_app.auth import require_registration
from bale_app.framework import (
    ConversationHandler,
    CallbackQueryHandler,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MessageFilter,
    MessageHandler,
    filters,
    track_prompt,
    delete_tracked,
)
from bale_app.keyboards import back_to_main_keyboard
from bale_app.jalali_calendar import (
    build_calendar_keyboard,
    parse_pick_data,
    parse_view_data,
)
from utils.busy_lock import clear_busy, is_busy, set_busy
from utils.dates import (
    gregorian_to_jalali_display,
    jalali_to_gregorian,
    validate_jalali_date_str,
)
import jdatetime

logger = logging.getLogger(__name__)

# States
(
    OBS_CONTENT,       # 0 — دریافت متن/ویس/عکس اولیه
    OBS_CONFIRM_VOICE, # 1 — تأیید/اصلاح متن ترنسکرایب‌شده
    OBS_EDIT_CHOICE,   # 2 — انتخاب جایگزین/افزودن برای اصلاح
    OBS_ATTACHMENTS,   # 3 — دریافت پیوست‌ها
    OBS_EXTEND,        # 4 — افزودن مطلب به مشاهده موجود
    OBS_TITLE,         # 5 — دریافت عنوان
    OBS_TAGS,          # 6 — دریافت هشتگ‌ها (اختیاری)
    OBS_DATE,          # 7 — دریافت تاریخ (اختیاری)
    OBS_SEARCH,        # 8 — دریافت عبارت جستجو
) = range(9)

_OBS_TMP_DIR = os.path.join(tempfile.gettempdir(), "knowledgebot_bale_obs")

# تجمیع پیام تأیید پیوست‌ها — کاربر چند عکس پشت‌سرهم بفرستد فقط یک پیام خلاصه می‌رود
_ATTACH_FLUSH_DELAY = 2.5
_attach_pending: dict[int, dict] = {}  # user_id -> {"photo": n, "file": n, "_task": Task}


async def _flush_attachments_notice(msg, context, user_id: int) -> None:
    """پس از مکث کوتاه، یک پیام جمعی برای همهٔ پیوست‌های تازه ذخیره‌شده می‌فرستد."""
    try:
        await asyncio.sleep(_ATTACH_FLUSH_DELAY)
    except asyncio.CancelledError:
        return
    slot = _attach_pending.pop(user_id, None)
    if not slot:
        return
    n_photo = slot.get("photo", 0)
    n_file = slot.get("file", 0)
    total = n_photo + n_file
    if total <= 0:
        return

    parts = []
    if n_photo:
        parts.append(f"🖼️ {n_photo} عکس")
    if n_file:
        parts.append(f"📎 {n_file} فایل")
    summary = " و ".join(parts)

    obs_id = context.user_data.get("obs_attach_obs_id") or context.user_data.get("obs_saved_id")
    # خلاصهٔ قبلی پیوست‌ها حذف و خلاصهٔ جدید جایگزین شود
    await delete_tracked(context, "_attach_summary")
    try:
        sent = await msg.reply_text(
            f"✅ {summary} ({total} مورد) ذخیره شد.\n\n"
            "می‌توانید پیوست دیگری اضافه کنید یا تمام کنید.",
            reply_markup=_attachments_confirm_keyboard(obs_id),
        )
        track_prompt(context, sent, "_attach_summary")
    except Exception:
        logger.exception("خطا در ارسال پیام جمعی پیوست‌ها")


def _status_label(status: str) -> str:
    labels = {
        "raw": "🟡 خام",
        "maturing": "🟠 در حال بررسی",
        "promoted": "🟢 تبدیل به دانش",
        "archived": "⚪ بایگانی",
    }
    return labels.get(status, status)


_OBS_PAGE_SIZE = 5

_VOICE_TITLE_PREFIX = "📝 متن تشخیص"


def _strip_voice_prefix(text: str | None) -> str:
    """حذف پیشوند قدیمی «متن تشخیص‌داده‌شده از ویس شما:» از ابتدای متن."""
    t = (text or "").lstrip()
    if t.startswith(_VOICE_TITLE_PREFIX):
        colon = t.find(":", len(_VOICE_TITLE_PREFIX))
        if 0 <= colon <= 80:
            t = t[colon + 1:]
    return t.strip()


def _obs_display_title(obs: dict) -> str:
    """عنوان نمایشی مشاهده؛ برای رکوردهای بی‌عنوان، اولین سطر تمیزِ محتوا."""
    title = (obs.get("title") or "").strip()
    if not title:
        content = _strip_voice_prefix(obs.get("content") or "")
        title = content.splitlines()[0] if content else ""
    return " ".join(title.split()) or "بدون عنوان"


def _obs_list_keyboard(obs_list: list[dict], page: int = 0) -> InlineKeyboardMarkup:
    """کیبورد لیست مشاهدات — ۵ آیتم در هر صفحه، بدون دکمهٔ ثبت جدید."""
    total = len(obs_list)
    start = page * _OBS_PAGE_SIZE
    chunk = obs_list[start:start + _OBS_PAGE_SIZE]
    rows = []
    for obs in chunk:
        label = f"{obs['id']} — {_obs_display_title(obs)[:38]}"
        rows.append([
            InlineKeyboardButton(label, callback_data=f"obs:view:{obs['id']}")
        ])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"obs:listpage:{page - 1}"))
    if start + _OBS_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"obs:listpage:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔍 جستجو", callback_data="obs:search")])
    rows.append([InlineKeyboardButton("🏠 منو", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def _obs_view_keyboard(obs: dict, attachments: list[dict] | None = None) -> InlineKeyboardMarkup:
    rows = []
    if obs.get("status") in ("raw", "maturing"):
        rows.append([InlineKeyboardButton("✏️ افزودن مطلب", callback_data=f"obs:extend:{obs['id']}")])
        rows.append([InlineKeyboardButton("📝 ارتقا به دانش", callback_data=f"obs:promote:{obs['id']}")])
        rows.append([InlineKeyboardButton("🗑 بایگانی", callback_data=f"obs:archive:{obs['id']}")])
    elif obs.get("status") == "promoted":
        rows.append([InlineKeyboardButton("🔗 مشاهده دانش", callback_data=f"kn:view:{obs['promoted_to_kn_id']}")])
    att_buttons = []
    for i, a in enumerate((attachments or [])[:6], start=1):
        mime = a.get("mime_type") or ""
        icon = "🖼️" if mime.startswith("image") else "📄"
        att_buttons.append(
            InlineKeyboardButton(f"{icon} پیوست {i}", callback_data=f"obs:getatt:{a['id']}")
        )
    for j in range(0, len(att_buttons), 2):
        rows.append(att_buttons[j:j + 2])
    rows.append([InlineKeyboardButton("🏠 بازگشت به منو", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def _attachments_confirm_keyboard(obs_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼️ افزودن عکس", callback_data=f"obs:add_photo:{obs_id}")],
        [InlineKeyboardButton("📎 افزودن فایل (PDF/...)", callback_data=f"obs:add_file:{obs_id}")],
        [InlineKeyboardButton("✅ تمام شد", callback_data="menu:main")],
    ])


def _voice_confirm_keyboard(obs_id: int | None) -> InlineKeyboardMarkup:
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


def _search_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 بر اساس عنوان/متن", callback_data="obs:search_keyword")],
        [InlineKeyboardButton("# بر اساس هشتگ", callback_data="obs:search_hashtag")],
        [InlineKeyboardButton("📅 بر اساس تاریخ", callback_data="obs:search_date")],
        [InlineKeyboardButton("🏠 بازگشت به منو", callback_data="menu:main")],
    ])


def _skip_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ رد کردن", callback_data="obs:skip")],
    ])


def _date_entry_keyboard() -> InlineKeyboardMarkup:
    """کیبورد مرحلهٔ تاریخ — تقویم یا رد کردن."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 انتخاب از تقویم", callback_data="obsdate:open")],
        [InlineKeyboardButton("⏭ رد کردن", callback_data="obs:skip")],
    ])


# ══════════════════════════════════════════════════════════════════════════════
# شروع ثبت مشاهده
# ══════════════════════════════════════════════════════════════════════════════

@require_registration
async def obs_new_start(update, context) -> int:
    """شروع ثبت مشاهده جدید."""
    query = update.callback_query
    await query.answer()
    context.user_data["obs_new_photos"] = []
    context.user_data["obs_new_files"] = []
    await query.edit_message_text(
        "📓 *ثبت مشاهده جدید*\n\n"
        "مشاهده‌ی خود را بنویسید، ویس بفرستید، یا عکس/فایل ضمیمه کنید.\n"
        "🎙️ *راهنما:* فارسی صحبت کنید — صدای شما توسط هوش مصنوعی به متن تبدیل می‌شود.\n\n"
        "پس از ثبت، عنوان، هشتگ و تاریخ را هم وارد می‌کنید.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 انصراف", callback_data="menu:main")],
        ]),
    )
    if query.message:
        track_prompt(context, query.message)
    return OBS_CONTENT


# ══════════════════════════════════════════════════════════════════════════════
# دریافت متن
# ══════════════════════════════════════════════════════════════════════════════

@require_registration
async def obs_content_received(update, context) -> int:
    """دریافت متن مشاهده."""
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("❌ متن خالی است. دوباره بنویسید یا ویس بفرستید:")
        return OBS_CONTENT
    return await _save_observation_text(update, context, text)


async def _save_observation_text(update, context, text: str) -> int:
    """ذخیره مشاهده در DB و رفتن به مرحلهٔ عنوان."""
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
        if update.message:
            await update.message.reply_text("❌ خطا در ثبت مشاهده. دوباره تلاش کنید.")
        return OBS_CONTENT

    context.user_data["obs_saved_id"] = obs_id

    # مرحلهٔ بعد: عنوان — پرامپت قبلی حذف شود
    target = update.effective_message
    if target is None:
        return ConversationHandler.END
    await delete_tracked(context)
    sent = await target.reply_text(
        f"✅ متن مشاهده ذخیره شد.\n\n"
        f"📝 حالا *عنوان* کوتاهی برای این مشاهده وارد کنید:\n"
        f"مثلاً: «نقص در شیرهای اطمینان واحد ۳»",
        parse_mode="Markdown",
    )
    track_prompt(context, sent)
    return OBS_TITLE


# ══════════════════════════════════════════════════════════════════════════════
# عنوان
# ══════════════════════════════════════════════════════════════════════════════

@require_registration
async def obs_title_received(update, context) -> int:
    """دریافت عنوان مشاهده."""
    title = (update.message.text or "").strip()
    if not title:
        await update.message.reply_text("❌ عنوان خالی است. یک عنوان کوتاه وارد کنید:")
        return OBS_TITLE
    obs_id = context.user_data.get("obs_saved_id")
    if obs_id:
        update_observation(obs_id, title=title)
    context.user_data["obs_title"] = title

    await delete_tracked(context)
    sent = await update.message.reply_text(
        "✅ عنوان ثبت شد.\n\n"
        "#️⃣ *هشتگ‌ها* (اختیاری):\n"
        "هشتگ‌ها را با فاصله بفرستید. مثال: `نقص فنی شیرآلات واحد۳`\n"
        "یا رد کنید.",
        parse_mode="Markdown",
        reply_markup=_skip_keyboard(),
    )
    track_prompt(context, sent)
    return OBS_TAGS


# ══════════════════════════════════════════════════════════════════════════════
# هشتگ‌ها (اختیاری)
# ══════════════════════════════════════════════════════════════════════════════

@require_registration
async def obs_tags_received(update, context) -> int:
    """دریافت هشتگ‌ها."""
    raw = (update.message.text or "").strip()
    tags = [t.strip() for t in raw.replace("،", " ").replace("#", "").split() if t.strip()]
    obs_id = context.user_data.get("obs_saved_id")
    if obs_id and tags:
        update_observation(obs_id, tags=tags)
    context.user_data["obs_tags"] = tags

    now_str = _today_jalali()
    await delete_tracked(context)
    sent = await update.message.reply_text(
        f"✅ هشتگ‌ها ثبت شد: {' '.join('#' + t for t in tags)}\n\n"
        f"📅 *تاریخ مشاهده* (اختیاری):\n"
        f"تاریخ را به فرمت `YYYY/MM/DD` وارد کنید، از تقویم انتخاب کنید، یا رد کنید (پیش‌فرض: {now_str}).",
        parse_mode="Markdown",
        reply_markup=_date_entry_keyboard(),
    )
    track_prompt(context, sent)
    return OBS_DATE


@require_registration
async def obs_tags_skip(update, context) -> int:
    """رد هشتگ‌ها."""
    query = update.callback_query
    await query.answer()
    context.user_data["obs_tags"] = []
    now_str = _today_jalali()
    await query.edit_message_text(
        "⏭ هشتگ ثبت نشد.\n\n"
        f"📅 *تاریخ مشاهده* (اختیاری):\n"
        f"تاریخ را به فرمت `YYYY/MM/DD` وارد کنید، از تقویم انتخاب کنید، یا رد کنید (پیش‌فرض: {now_str}).",
        parse_mode="Markdown",
        reply_markup=_date_entry_keyboard(),
    )
    return OBS_DATE


# ══════════════════════════════════════════════════════════════════════════════
# تاریخ (اختیاری)
# ══════════════════════════════════════════════════════════════════════════════

def _today_jalali() -> str:
    """تاریخ امروز به فرمت جلالی YYYY/MM/DD."""
    return jdatetime.date.today().strftime("%Y/%m/%d")


@require_registration
async def obs_date_received(update, context) -> int:
    """دریافت تاریخ مشاهده."""
    raw = (update.message.text or "").strip()
    valid, err = validate_jalali_date_str(raw)
    if not valid:
        await delete_tracked(context)
        sent = await update.message.reply_text(
            f"❌ {err}\nفرمت صحیح: `1402/12/15` — دوباره وارد کنید یا از تقویم انتخاب کنید.",
            parse_mode="Markdown",
            reply_markup=_date_entry_keyboard(),
        )
        track_prompt(context, sent)
        return OBS_DATE

    # تبدیل به میلادی برای ذخیره
    greg_date = jalali_to_gregorian(raw)
    obs_id = context.user_data.get("obs_saved_id")
    if obs_id:
        update_observation(obs_id, obs_date=greg_date)
    context.user_data["obs_date_jalali"] = raw

    return await _obs_final_confirm(update, context)


@require_registration
async def obs_date_skip(update, context) -> int:
    """رد تاریخ — استفاده از تاریخ امروز."""
    query = update.callback_query
    await query.answer()
    now_str = _today_jalali()
    greg_date = jalali_to_gregorian(now_str)
    obs_id = context.user_data.get("obs_saved_id")
    if obs_id:
        update_observation(obs_id, obs_date=greg_date)
    context.user_data["obs_date_jalali"] = now_str
    return await _obs_final_confirm(update, context)


# ── تقویم جلالی ──

@require_registration
async def obs_cal_open(update, context) -> int:
    """باز کردن تقویم برای انتخاب تاریخ مشاهده."""
    query = update.callback_query
    await query.answer()
    today = jdatetime.date.today()
    context.user_data["obs_cal_view"] = [today.year, today.month]
    await query.edit_message_text(
        "📅 *انتخاب تاریخ مشاهده* — روز موردنظر را بزنید:",
        parse_mode="Markdown",
        reply_markup=build_calendar_keyboard("obsdate", today.year, today.month),
    )
    return OBS_DATE


@require_registration
async def obs_cal_nav(update, context) -> int:
    """ناوبری ماه/سال در تقویم."""
    query = update.callback_query
    await query.answer()
    parsed = parse_view_data(query.data or "")
    if not parsed:
        return OBS_DATE
    year, month = parsed
    context.user_data["obs_cal_view"] = [year, month]
    await query.edit_message_text(
        "📅 *انتخاب تاریخ مشاهده* — روز موردنظر را بزنید:",
        parse_mode="Markdown",
        reply_markup=build_calendar_keyboard("obsdate", year, month),
    )
    return OBS_DATE


@require_registration
async def obs_cal_none(update, context) -> int:
    """کلیک روی سلول غیرفعال — هیچ کاری نکن."""
    try:
        await update.callback_query.answer()
    except Exception:
        pass
    return OBS_DATE


@require_registration
async def obs_cal_pick(update, context) -> int:
    """انتخاب روز از تقویم — همان مسیر ورودی متنی."""
    query = update.callback_query
    await query.answer()
    parsed = parse_pick_data(query.data or "")
    if not parsed:
        return OBS_DATE
    year, month, day = parsed
    raw = f"{year}/{month:02d}/{day:02d}"

    greg_date = jalali_to_gregorian(raw)
    obs_id = context.user_data.get("obs_saved_id")
    if obs_id:
        update_observation(obs_id, obs_date=greg_date)
    context.user_data["obs_date_jalali"] = raw
    return await _obs_final_confirm(update, context)


async def _obs_final_confirm(update, context) -> int:
    """نمایش خلاصه و ذخیره نهایی + گزینه پیوست."""
    obs_id = context.user_data.get("obs_saved_id")
    title = context.user_data.get("obs_title", "—")
    content = _get_content_from_context(context)
    tags = context.user_data.get("obs_tags", [])
    date_str = context.user_data.get("obs_date_jalali", _today_jalali())

    if not obs_id:
        return ConversationHandler.END

    tag_line = " ".join("#" + t for t in tags) if tags else "—"

    target = update.effective_message
    if target is None:
        return ConversationHandler.END

    # پرامپت قبلی (تاریخ/تقویم) و خلاصهٔ قبلی پیوست‌ها حذف شوند
    await delete_tracked(context)
    await delete_tracked(context, "_attach_summary")
    context.user_data["obs_attach_prompt_cleaned"] = False

    sent = await target.reply_text(
        f"✅ *مشاهده کامل ثبت شد*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📝 *عنوان:* {title}\n"
        f"📅 *تاریخ:* {date_str}\n"
        f"#️⃣ *هشتگ‌ها:* {tag_line}\n\n"
        f"{content[:200]}\n\n"
        "آیا می‌خواهید ضمیمه‌ای (عکس، PDF، فایل) اضافه کنید؟",
        parse_mode="Markdown",
        reply_markup=_attachments_confirm_keyboard(obs_id),
    )
    track_prompt(context, sent)
    return OBS_ATTACHMENTS


def _get_content_from_context(context) -> str:
    """برگرداندن محتوای مشاهده از context یا DB."""
    obs_id = context.user_data.get("obs_saved_id")
    if obs_id:
        obs = get_observation_by_id(obs_id)
        if obs:
            return obs.get("content", "")
    return ""


# ══════════════════════════════════════════════════════════════════════════════
# دریافت ویس + تأیید/اصلاح
# ══════════════════════════════════════════════════════════════════════════════

@require_registration
async def obs_voice_received(update, context) -> int:
    """دریافت ویس برای مشاهده — متن ترنسکرایب‌شده را نشان می‌دهد و تأیید می‌گیرد."""
    from bale_app.knowledge import transcribe_voice

    user = update.effective_user
    if user and is_busy(user.id):
        await update.message.reply_text("⏳ هوش مصنوعی در حال بررسی است... لطفاً کمی صبر کنید.")
        return OBS_CONTENT
    if user:
        set_busy(user.id)
    try:
        text = await transcribe_voice(update, context)
    finally:
        if user:
            clear_busy(user.id)
    if text is None:
        return OBS_CONTENT

    context.user_data["obs_voice_text"] = text

    await delete_tracked(context)
    # دکمهٔ «📋 کپی متن» بله تا ~۲۵۶ کاراکتر پشتیبانی می‌کند — یک لمس، کپی دقیق
    kb_rows = _voice_confirm_keyboard(None).keyboard
    if len(text) <= 250:
        kb_rows.insert(
            0, [InlineKeyboardButton("📋 کپی متن", copy_text=text)]
        )
    sent = await update.message.reply_text(
        f"📝 *متن تشخیص‌داده‌شده از ویس شما:*\n\n{text}\n\n"
        "آیا این متن درست است؟ می‌توانید اصلاح کنید.",
        reply_markup=InlineKeyboardMarkup(kb_rows),
    )
    track_prompt(context, sent)
    return OBS_CONFIRM_VOICE


@require_registration
async def obs_confirm_voice(update, context) -> int:
    query = update.callback_query
    await query.answer()
    text = context.user_data.get("obs_voice_text", "")
    if not text:
        await query.edit_message_text("❌ متنی یافت نشد. دوباره ویس بفرستید.")
        return OBS_CONTENT
    return await _save_observation_text(update, context, text)


@require_registration
async def obs_edit_voice_start(update, context) -> int:
    """
    ورود به حالت اصلاح — پیام حاوی متن ترنسکرایب دست نخورده می‌ماند
    (کاربر آن را کپی/ویرایش می‌کند) و فقط راهنما به‌صورت پیام جدید ارسال می‌شود.
    """
    query = update.callback_query
    await query.answer()
    text = context.user_data.get("obs_voice_text", "")
    if not text:
        await query.edit_message_text("❌ متنی یافت نشد. دوباره ویس بفرستید.")
        return OBS_CONTENT
    # متن خام را دوباره می‌فرستیم تا کاربر مطمئن باشد چه چیزی را ویرایش می‌کند
    sent = await update.effective_message.reply_text(text)
    track_prompt(context, sent)
    sent2 = await update.effective_message.reply_text(
        "✏️ متن بالا (یا پیام قبلی) را کپی، ویرایش کن و نتیجه را همین‌جا بفرست:"
    )
    track_prompt(context, sent2)
    return OBS_CONFIRM_VOICE


@require_registration
async def obs_edit_voice_text(update, context) -> int:
    corrected = (update.message.text or "").strip()
    if not corrected:
        await update.message.reply_text("❌ متن خالی است. دوباره بنویسید:")
        return OBS_CONFIRM_VOICE

    context.user_data["obs_edited_text"] = corrected
    await delete_tracked(context)
    sent = await update.message.reply_text(
        "🔄 این متن اصلاح‌شده:\n\n"
        f"{corrected[:200]}\n\n"
        "آیا می‌خواهید این متن جایگزین متن تشخیص‌داده‌شده شود یا به آن اضافه شود؟",
        parse_mode="Markdown",
        reply_markup=_edit_choice_keyboard(),
    )
    track_prompt(context, sent)
    return OBS_EDIT_CHOICE


@require_registration
async def obs_edit_replace(update, context) -> int:
    query = update.callback_query
    await query.answer()
    corrected = context.user_data.get("obs_edited_text", "")
    if not corrected:
        await query.edit_message_text("❌ متنی برای ذخیره یافت نشد.")
        return OBS_CONTENT
    context.user_data["obs_voice_text"] = corrected
    return await _save_observation_text(update, context, corrected)


@require_registration
async def obs_edit_append(update, context) -> int:
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

def _choose_photo(photos) -> object:
    """انتخاب سایز نزدیک به ~800px برای صرفه‌جویی فضا (معادل سایز متوسط تلگرام)."""
    if not photos:
        return None
    if len(photos) >= 3:
        return sorted(photos, key=lambda p: p.width or 0)[-2]
    return photos[-1]


@require_registration
async def obs_add_photo_start(update, context) -> int:
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
async def obs_add_file_start(update, context) -> int:
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
async def obs_attachment_received(update, context) -> int:
    obs_id = context.user_data.get("obs_attach_obs_id") or context.user_data.get("obs_saved_id")
    if not obs_id:
        await update.message.reply_text("❌ شناسه مشاهده یافت نشد. دوباره /start بزنید.")
        return OBS_ATTACHMENTS

    msg = update.message
    file_id = None
    file_name = None
    mime_type = None

    if msg.photo:
        chosen = _choose_photo(msg.photo)
        file_id = chosen.file_id
        file_name = f"photo_{file_id[:12]}.jpg"
        mime_type = "image/jpeg"
    elif msg.document:
        doc = msg.document
        file_id = doc.file_id
        file_name = getattr(doc, "file_name", None) or f"file_{file_id[:12]}"
        mime_type = getattr(doc, "mime_type", None) or "application/octet-stream"

    if not file_id:
        await update.message.reply_text("❌ فقط عکس و فایل پشتیبانی می‌شود. دوباره بفرستید:")
        return OBS_ATTACHMENTS

    try:
        # get_file در بله مستقیماً bytes برمی‌گرداند
        data = await context.bot.get_file(file_id)
        obs_dir = os.path.join(config.OBS_ATTACH_PATH, str(obs_id))
        os.makedirs(obs_dir, exist_ok=True)
        base, ext = os.path.splitext(file_name or "file")
        dest = os.path.join(obs_dir, file_name or f"attachment_{file_id[:12]}")
        counter = 1
        while os.path.exists(dest):
            dest = os.path.join(obs_dir, f"{base}_{counter}{ext}")
            counter += 1

        with open(dest, "wb") as fh:
            fh.write(data)
        file_size = os.path.getsize(dest)

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

    # پیام تأیید فوری نمی‌فرستیم — با مکث کوتاه تجمیع می‌شود
    # اولین پیوست: پیام خلاصهٔ ثبت (با کیبورد پیوست) حذف شود
    if not context.user_data.get("obs_attach_prompt_cleaned"):
        await delete_tracked(context)
        context.user_data["obs_attach_prompt_cleaned"] = True

    kind = "photo" if mime_type and mime_type.startswith("image") else "file"
    uid = update.effective_user.id if update.effective_user else 0
    slot = _attach_pending.setdefault(uid, {})
    slot[kind] = slot.get(kind, 0) + 1
    old_task = slot.get("_task")
    if old_task:
        old_task.cancel()
    slot["_task"] = asyncio.create_task(
        _flush_attachments_notice(update.message, context, uid)
    )
    return OBS_ATTACHMENTS


# ══════════════════════════════════════════════════════════════════════════════
# لیست مشاهدات
# ══════════════════════════════════════════════════════════════════════════════

@require_registration
async def obs_list_start(update, context) -> int:
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

    context.user_data["obs_list_source"] = "all"
    await query.edit_message_text(
        f"📂 *مشاهدات شما* ({len(obs_list)} مورد)",
        parse_mode="Markdown",
        reply_markup=_obs_list_keyboard(obs_list, 0),
    )
    return ConversationHandler.END


@require_registration
async def obs_list_page(update, context) -> int:
    """صفحه‌بندی لیست مشاهدات. (callback: obs:listpage:N)"""
    query = update.callback_query
    await query.answer()
    page = int((query.data or "").split(":")[2])
    user = update.effective_user
    if not user:
        return ConversationHandler.END
    if context.user_data.get("obs_list_source") == "search":
        obs_list = context.user_data.get("obs_search_results") or []
        header = "🔍 *نتایج جستجو*"
    else:
        obs_list = list_observations_by_user(user.id)
        header = f"📂 *مشاهدات شما* ({len(obs_list)} مورد)"
    if not obs_list:
        return ConversationHandler.END
    pages = max(1, (len(obs_list) + _OBS_PAGE_SIZE - 1) // _OBS_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    await query.edit_message_text(
        header, parse_mode="Markdown",
        reply_markup=_obs_list_keyboard(obs_list, page),
    )
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# مشاهده جزئیات
# ══════════════════════════════════════════════════════════════════════════════

@require_registration
async def obs_view(update, context) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    obs_id = int(data.split(":")[2])

    obs = get_observation_by_id(obs_id)
    if not obs:
        await query.edit_message_text("⚠️ مشاهده یافت نشد.", reply_markup=back_to_main_keyboard())
        return ConversationHandler.END

    # تاریخ
    date_display = ""
    if obs.get("obs_date"):
        try:
            date_display = f"📅 {gregorian_to_jalali_display(obs['obs_date'])}\n"
        except Exception:
            pass

    # هشتگ‌ها
    tags = obs.get("tags") or "[]"
    import json
    tag_list = json.loads(tags) if isinstance(tags, str) else (tags or [])
    tag_line = " ".join("#" + t for t in tag_list) if tag_list else ""

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

    title = obs.get("title") or "—"
    content = obs.get("content") or ""
    created = obs.get("created_at", "")
    try:
        created_fa = created[:16].replace("T", " ")
    except Exception:
        created_fa = created

    text = (
        f"📓 *#{obs_id} — {title}*\n"
        f"وضعیت: {_status_label(obs['status'])}\n"
        f"{date_display}"
        f"زمان ثبت: {created_fa}\n"
        + (f"*{tag_line}*\n" if tag_line else "")
        + f"\n{content}"
        + "\n".join(att_lines)
    )
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=_obs_view_keyboard(obs, attachments))


@require_registration
async def obs_attachment_send(update, context) -> int:
    """ارسال واقعی فایل پیوست مشاهده. (callback: obs:getatt:<id>)"""
    query = update.callback_query
    await query.answer()
    att_id = int((query.data or "").split(":")[2])

    att = get_observation_attachment(att_id)
    if not att:
        await query.message.reply_text("⚠️ پیوست یافت نشد.")
        return ConversationHandler.END

    user = update.effective_user
    profile = get_user_by_telegram_id(user.id) if user else None
    obs = get_observation_by_id(att["observation_id"])
    if not profile or not obs or obs["telegram_id"] != profile["telegram_id"]:
        await query.message.reply_text("⛔ این پیوست به حساب شما تعلق ندارد.")
        return ConversationHandler.END

    path = att.get("file_path") or ""
    if not path or not os.path.isfile(path):
        await query.message.reply_text("❌ فایل روی سرور یافت نشد.")
        return ConversationHandler.END

    mime = (att.get("mime_type") or "").lower()
    name = os.path.basename(path)
    try:
        if mime.startswith("image"):
            await query.message.reply_photo(path, caption=name)
        else:
            await query.message.reply_document(path, caption=name)
    except Exception:
        logger.exception("خطا در ارسال پیوست %s", att_id)
        await query.message.reply_text("❌ خطا در ارسال فایل. دوباره تلاش کنید.")
    return ConversationHandler.END
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# افزودن مطلب به مشاهده موجود
# ══════════════════════════════════════════════════════════════════════════════

@require_registration
async def obs_extend_start(update, context) -> int:
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
        f"محتوای فعلی:\n{(obs.get('content') or '')[:300]}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تمام شد", callback_data="menu:main")],
        ]),
    )
    return OBS_EXTEND


@require_registration
async def obs_extend_received(update, context) -> int:
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
# جستجو
# ══════════════════════════════════════════════════════════════════════════════

@require_registration
async def obs_search_start(update, context) -> int:
    """نمایش منوی جستجو."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔍 *جستجو در مشاهدات*\n\n"
        "بر اساس کدام معیار جستجو کنم؟",
        parse_mode="Markdown",
        reply_markup=_search_keyboard(),
    )
    return ConversationHandler.END


@require_registration
async def obs_search_keyword(update, context) -> int:
    """شروع جستجوی متنی."""
    query = update.callback_query
    await query.answer()
    context.user_data["obs_search_mode"] = "keyword"
    await query.edit_message_text(
        "🔍 *جستجوی متنی*\n\n"
        "عبارت مورد نظر را بنویسید — در عنوان و متن مشاهده جستجو می‌شود:\n"
        "مثال: `شیر اطمینان`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 بازگشت به منو", callback_data="menu:main")],
        ]),
    )
    return OBS_SEARCH


@require_registration
async def obs_search_hashtag(update, context) -> int:
    """شروع جستجوی هشتگ."""
    query = update.callback_query
    await query.answer()
    context.user_data["obs_search_mode"] = "hashtag"
    await query.edit_message_text(
        "#️⃣ *جستجوی هشتگ*\n\n"
        "هشتگ مورد نظر را بنویسید (بدون #):\n"
        "مثال: `نقص فنی`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 بازگشت به منو", callback_data="menu:main")],
        ]),
    )
    return OBS_SEARCH


@require_registration
async def obs_search_date(update, context) -> int:
    """شروع جستجوی تاریخ."""
    query = update.callback_query
    await query.answer()
    context.user_data["obs_search_mode"] = "date"
    await query.edit_message_text(
        "📅 *جستجوی تاریخ*\n\n"
        "تاریخ را به فرمت `YYYY/MM/DD` وارد کنید:\n"
        "مثال: `1402/12/15`\n\n"
        "*(برای جستجوی یک ماه کامل، فقط سال و ماه بزنید: `1402/12`)*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 بازگشت به منو", callback_data="menu:main")],
        ]),
    )
    return OBS_SEARCH


@require_registration
async def obs_search_query(update, context) -> int:
    """دریافت عبارت جستجو و نمایش نتایج."""
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    mode = context.user_data.get("obs_search_mode", "keyword")
    raw = (update.message.text or "").strip()
    if not raw:
        await update.message.reply_text("❌ عبارت خالی است. دوباره بنویسید:")
        return OBS_SEARCH

    results = []
    if mode == "keyword":
        results = search_observations(user.id, keyword=raw)
    elif mode == "hashtag":
        clean = raw.replace("#", "").strip()
        results = search_observations(user.id, hashtag=clean)
    elif mode == "date":
        # تاریخ جلالی → میلادی
        try:
            greg_date = jalali_to_gregorian(raw)
        except Exception:
            await update.message.reply_text(
                "❌ فرمت تاریخ نامعتبر. از `YYYY/MM/DD` استفاده کنید (مثال: `1402/12/15`).",
                parse_mode="Markdown",
            )
            return OBS_SEARCH
        results = search_observations(user.id, obs_date=greg_date)

    if not results:
        await update.message.reply_text(
            "🔍 هیچ مشاهده‌ای با این معیار یافت نشد.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 جستجوی دیگر", callback_data="obs:search")],
                [InlineKeyboardButton("🏠 بازگشت به منو", callback_data="menu:main")],
            ]),
        )
        return ConversationHandler.END

    context.user_data["obs_search_results"] = results
    context.user_data["obs_list_source"] = "search"
    await update.message.reply_text(
        f"🔍 *نتایج جستجو* ({len(results)} مورد)",
        parse_mode="Markdown",
        reply_markup=_obs_list_keyboard(results, 0),
    )
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# ارتقا به دانش / بایگانی
# ══════════════════════════════════════════════════════════════════════════════

@require_registration
async def obs_promote(update, context) -> int:
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
async def obs_archive(update, context) -> int:
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
# فیلترها
# ══════════════════════════════════════════════════════════════════════════════

class _AudioMessageFilter(MessageFilter):
    def filter(self, message):
        if not message:
            return False
        if message.voice or message.audio or message.video_note:
            return True
        if message.document and getattr(message.document, "mime_type", None):
            return message.document.mime_type.startswith("audio")
        return False


AUDIO_MESSAGE_FILTER = _AudioMessageFilter()


class _PhotoDocFilter(MessageFilter):
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
            CallbackQueryHandler(obs_list_page, pattern=r"^obs:listpage:\d+$"),
            CallbackQueryHandler(obs_view, pattern=r"^obs:view:\d+$"),
            CallbackQueryHandler(obs_attachment_send, pattern=r"^obs:getatt:\d+$"),
            CallbackQueryHandler(obs_extend_start, pattern=r"^obs:extend:\d+$"),
            CallbackQueryHandler(obs_promote, pattern=r"^obs:promote:\d+$"),
            CallbackQueryHandler(obs_archive, pattern=r"^obs:archive:\d+$"),
            CallbackQueryHandler(obs_search_start, pattern=r"^obs:search$"),
            CallbackQueryHandler(obs_search_keyword, pattern=r"^obs:search_keyword$"),
            CallbackQueryHandler(obs_search_hashtag, pattern=r"^obs:search_hashtag$"),
            CallbackQueryHandler(obs_search_date, pattern=r"^obs:search_date$"),
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
            OBS_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, obs_title_received),
            ],
            OBS_TAGS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, obs_tags_received),
                CallbackQueryHandler(obs_tags_skip, pattern=r"^obs:skip$"),
            ],
            OBS_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, obs_date_received),
                CallbackQueryHandler(obs_date_skip, pattern=r"^obs:skip$"),
                CallbackQueryHandler(obs_cal_open, pattern=r"^obsdate:open$"),
                CallbackQueryHandler(obs_cal_nav, pattern=r"^obsdate:view:\d+:\d+$"),
                CallbackQueryHandler(obs_cal_none, pattern=r"^obsdate:none$"),
                CallbackQueryHandler(obs_cal_pick, pattern=r"^obsdate:pick:\d+:\d+:\d+$"),
            ],
            OBS_SEARCH: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, obs_search_query),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(obs_list_start, pattern=r"^obs:list$"),
        ],
        per_message=False,
        name="observations",
        persistent=False,
        allow_reentry=True,
    )
