"""
ماژول knowledge — ثبت دانش/تجربه سازمانی از طریق بله (فاز دانش سازمانی) — نسخه بله.

فلوی مکالمه:
    روش (دستی/مصاحبه) ← نوع دانش ← نام گزارش‌دهنده ← سمت
    ← شرح آزاد تجربه ← [استخراج فیلدها با AI ← پیشنهاد طبقه‌بندی (اختیاری)
        ← پرسش فیلدهای ناقص یک‌به‌یک (شامل «تاثیر اجرای پیشنهاد» برای پیشنهاد)]
    ← عکس‌ها (اختیاری) ← تاریخ ← پیش‌نمایش (پیش‌نویس DANA) ← ثبت نهایی
        (شماره‌دهی خودکار KN + ارسال پیش‌نویس DANA + فایل‌های PDF و DOCX)

(پورت از handlers/knowledge.py — منبع import تغییر کرده، تبدیل ویس به متن
برای API بله تطبیق یافته، و دو باگ فراخوانی «.callback» اصلاح شده است.)
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile

import jdatetime

import config
from bale import InputFile

from db import models
from db.models import (
    get_user_by_telegram_id,
    add_knowledge_entry,
    get_knowledge_entry_by_id,
    list_knowledge_by_user,
    search_knowledge_by_user,
    set_knowledge_fields,
    submit_knowledge_entry,
    set_knowledge_inactive,
    add_knowledge_photo,
    list_knowledge_photos,  # noqa: F401 — سازگاری با نسخه تلگرام
)
from bale_app.auth import require_registration
from bale_app.framework import (
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MessageFilter,
    MessageHandler,
    filters,
)
from bale_app.keyboards import main_menu_keyboard, back_to_main_keyboard
from bale_app.framework import track_prompt, delete_tracked
from bale_app.jalali_calendar import (
    build_calendar_keyboard,
    parse_pick_data,
    parse_view_data,
)
from utils.busy_lock import clear_busy, is_busy, set_busy
from utils.dates import validate_jalali_date_str
from engine.knowledge_ai import extract_fields, FIELD_SCHEMAS, TYPE_LABELS
from engine.knowledge_draft import build_report, render_text, entry_display_title
from engine.knowledge_render import render_dana_pdf, render_dana_docx

logger = logging.getLogger(__name__)


async def _prompt_reply(target, context, text, reply_markup=None):
    """پرامپت قبلی حذف، پیام جدید ارسال و track میشود — چت تمیز میماند."""
    await delete_tracked(context)
    sent = await target.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    track_prompt(context, sent)
    return sent

# ══════════════════════════════════════════════════════════════════════════════
# state ها
# ══════════════════════════════════════════════════════════════════════════════

(
    KN_MODE_SELECT,        # زیرمنو: روش دستی / مصاحبه با AI
    KN_TYPE,               # نوع دانش
    KN_REPORTER_NAME,      # نام گزارش‌دهنده
    KN_REPORTER_TITLE,     # سمت (اختیاری)
    KN_DESCRIPTION,        # شرح آزاد تجربه (روش دستی قدیم - هیبریدی)
    KN_FIELD_ANSWER,       # پرسش فیلدهای ناقص (هیبریدی)
    KN_INTERVIEW_FRAMEWORK, # نمایش چارچوب راهنما (روش مصاحبه)
    KN_INTERVIEW_LOOP,     # حلقهٔ مصاحبه با AI
    KN_FINAL_ASSEMBLE,     # پاس AI polish نهایی (transient)
    KN_ORG_META,           # تنظیمات سازمانی (درخت/کمیته/بذر/همکاران/هشتگ/محدوده)
    KN_TREE,               # انتخاب درخت دانش (sub-flow)
    KN_PREVIEW,            # پیشنمایش + ۳ کلید
    KN_FIELD_EDIT,         # ویرایش یک فیلد از preview
    KN_PHOTOS,             # عکس/مدرک
    KN_DATE,               # تاریخ ثبت
    KN_FINISH,             # ثبت نهایی + ساخت PDF/DOCX + ارسال
    KN_TYPE_CONFIRM,       # تأیید نوع پس از پیشنهاد AI (تعارض طبقه‌بندی)
    KN_ARCHIVE_LIST,       # بایگانی دانش — لیست صفحه‌بندی‌شده/نتایج جستجو
    KN_SEARCH_INPUT,       # دریافت عبارت جستجو در دانش‌ها
    KN_FIELD_DIRECT,       # پرسش مستقیم فیلدهای دانا (دستیِ خالصِ بدون AI)
) = range(20)

_KEY_ENTRY_ID = "kn_entry_id"

_MAX_NAME = 100
_MAX_DESC = 4000
_MAX_FIELD = 1000


# ══════════════════════════════════════════════════════════════════════════════
# کمکی
# ══════════════════════════════════════════════════════════════════════════════

def _clean_text(value: str | None, max_len: int) -> str | None:
    t = (value or "").strip()
    if not t or len(t) > max_len:
        return None
    return t


def _input_text(update, context) -> str:
    """
    متن ورودی کاربر را برمی‌گرداند.
    اگر پیام ویس بوده، متن ترنسکرایب‌شده در kn_pending_text ذخیره شده است.
    """
    pending = context.user_data.pop("kn_pending_text", None)
    if pending:
        return pending
    return (update.message.text or "") if update.message else ""


# ── قفل «در حال پردازش» هوش مصنوعی ──────────────────────────────────────────

def _busy_ai(func):
    """دکوراتور async: قبل از فراخوانی تابع قفل می‌گذارد، بعد پاک می‌کند.
    اگر کاربر قبلاً در حال پردازش باشد (قفل قبلی فعال)، پیام ⏳ می‌گیرد.
    """
    import functools

    @functools.wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        user = update.effective_user
        tid = user.id if user else None
        if tid:
            if is_busy(tid):
                # کاربر هنوز مشغول است — پیام بده و از ادامه جلوگیری کن
                try:
                    if update.callback_query:
                        await update.callback_query.answer(
                            "⏳ هوش مصنوعی در حال بررسی است... لطفاً کمی صبر کنید.",
                            show_alert=True,
                        )
                    elif update.message:
                        await update.message.reply_text(
                            "⏳ هوش مصنوعی در حال بررسی است... لطفاً کمی صبر کنید.",
                        )
                except Exception:
                    logger.exception("خطا در ارسال پیام busy")
                return ConversationHandler.END
            set_busy(tid)
        try:
            return await func(update, context, *args, **kwargs)
        finally:
            if tid:
                clear_busy(tid)
    return wrapper


# ── تبدیل گفتار به متن (STT) — Groq Whisper ──────────────────────────────────

_STT_TMP_DIR = os.path.join(tempfile.gettempdir(), "knowledgebot_bale_stt")


async def transcribe_voice(update, context) -> str | None:
    """ویس/فایل صوتی بله را دانلود و با Groq Whisper به متن تبدیل می‌کند.

    تفاوت با تلگرام: bot.get_file در بله مستقیماً bytes برمی‌گرداند.

    خروجی: متن استخراج‌شده یا None در صورت خطا.
    """
    import config as _config

    if not _config.GROQ_API_KEY:
        await update.message.reply_text(
            "⚠️ تبدیل صدا به متن پیکربندی نشده است. لطفاً به‌جای ویس، متن بنویسید."
        )
        return None

    import httpx

    # ۱. پیدا کردن فایل صوتی (voice / audio / document صوتی — video_note در بله نیست)
    msg = update.message
    file_id = None
    if msg.voice:
        file_id = msg.voice.file_id
    elif msg.audio:
        file_id = msg.audio.file_id
    elif msg.document and getattr(msg.document, "mime_type", None) \
            and msg.document.mime_type.startswith("audio"):
        file_id = msg.document.file_id

    if not file_id:
        await update.message.reply_text("❌ فایل صوتی شناخته‌شده‌ای پیدا نکردم. لطفاً ویس بفرستید یا متن بنویسید.")
        return None

    tmp_path = os.path.join(_STT_TMP_DIR, f"voice_{update.effective_user.id}_{file_id[:20]}.ogg")

    # ۲. دانلود فایل — get_file در بله bytes برمی‌گرداند
    try:
        data = await context.bot.get_file(file_id)
        os.makedirs(_STT_TMP_DIR, exist_ok=True)
        with open(tmp_path, "wb") as fh:
            fh.write(data)
    except Exception:
        logger.exception("خطا در دانلود فایل صوتی")
        await update.message.reply_text("❌ دانلود فایل صوتی ناموفق بود. دوباره تلاش کنید یا متن بنویسید.")
        return None

    # ۳. ارسال به Groq Whisper
    try:
        url = f"{_config.GROQ_STT_BASE_URL.rstrip('/')}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {_config.GROQ_API_KEY}"}
        with open(tmp_path, "rb") as f:
            files = {"file": (os.path.basename(tmp_path), f, "audio/ogg")}
            data_form = {"model": _config.GROQ_STT_MODEL, "language": "fa", "response_format": "json"}
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, headers=headers, files=files, data=data_form)
        if resp.status_code != 200:
            logger.error("Groq STT خطا: %s — %s", resp.status_code, resp.text[:200])
            await update.message.reply_text(
                f"❌ تبدیل صدا ناموفق بود (کد {resp.status_code}). لطفاً دوباره ویس بفرستید یا متن بنویسید."
            )
            return None
        text = (resp.json().get("text") or "").strip()
        if not text:
            await update.message.reply_text("❌ صدایی تشخیص داده نشد. لطفاً واضح‌تر صحبت کنید یا متن بنویسید.")
            return None
        return text
    except Exception:
        logger.exception("خطا در Groq STT")
        await update.message.reply_text("❌ خطا در تبدیل صدا به متن. لطفاً دوباره تلاش کنید یا متن بنویسید.")
        return None
    finally:
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


async def kn_voice_message(update, context) -> int:
    """هندلر سراسری ویس/فایل صوتی در مکالمهٔ ثبت دانش."""
    text = await transcribe_voice(update, context)
    if text is None:
        return ConversationHandler.END

    context.user_data["kn_pending_text"] = text

    # حالت فعلی از context.user_data
    mode = context.user_data.get("kn_mode")
    current_field = context.user_data.get("kn_current_field")

    if current_field:
        # در حال پاسخ به فیلد ناقص
        return await kn_field_answer(update, context)
    if mode == "interview":
        # در حال مصاحبه با AI
        return await kn_interview_loop_text(update, context)
    # حالت‌های دستی: نام/سمت/شرح
    if not context.user_data.get("kn_reporter_name"):
        return await kn_reporter_name(update, context)
    if not context.user_data.get("kn_reporter_title"):
        return await kn_reporter_title(update, context)
    if not context.user_data.get("kn_description"):
        return await kn_description(update, context)
    # پیش‌فرض: مصاحبه
    return await kn_interview_loop_text(update, context)


def _voice_handler_for(target_handler):
    """Factory: یک هندلر ویس می‌سازد که صدا را به متن تبدیل و به target_handler می‌دهد."""
    async def wrapper(update, context) -> int:
        text = await transcribe_voice(update, context)
        if text is None:
            return ConversationHandler.END
        context.user_data["kn_pending_text"] = text
        return await target_handler(update, context)
    return wrapper


class _AudioMessageFilter(MessageFilter):
    """فیلتر پیام‌های صوتی: voice / audio / document صوتی."""

    def filter(self, message):
        if not message:
            return False
        if message.voice or message.audio or message.video_note:
            return True
        if message.document and getattr(message.document, "mime_type", None):
            return message.document.mime_type.startswith("audio")
        return False


AUDIO_MESSAGE_FILTER = _AudioMessageFilter()


def _mode_select_keyboard(ai_available: bool) -> InlineKeyboardMarkup:
    """زیرمنوی انتخاب روش ثبت دانش."""
    rows: list[list[InlineKeyboardButton]] = []
    if ai_available:
        rows.append([
            InlineKeyboardButton(
                "🎙️ مصاحبه با هوش مصنوعی (پیشنهادی)",
                callback_data="kn_mode:interview",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            "📋 ثبت مستقیم فرمی (بدون AI / سریع)",
            callback_data="kn_mode:direct",
        )
    ])
    rows.append([InlineKeyboardButton("🏠 منو", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def _type_buttons() -> list[list[InlineKeyboardButton]]:
    """دکمه‌های انتخاب نوع دانش (درس‌آموخته / پیشنهاد / دانش صریح)."""
    return [
        [InlineKeyboardButton(label, callback_data=f"kn_type:{key}")]
        for key, label in TYPE_LABELS.items()
    ]


def _type_conflict_keyboard(recommended: str) -> InlineKeyboardMarkup:
    """کیبورد پیشنهاد طبقه‌بندی AI وقتی با انتخاب اپراتور تعارض دارد."""
    rows = [[InlineKeyboardButton("✅ انتخاب من را نگه دار", callback_data="kn_type_keep")]]
    if recommended in TYPE_LABELS:
        rows.append([InlineKeyboardButton(
            f"🔄 تغییر به «{TYPE_LABELS[recommended]}»",
            callback_data=f"kn_type_switch:{recommended}",
        )])
    rows.append([InlineKeyboardButton("🏠 منو", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def _field_skip_keyboard() -> InlineKeyboardMarkup:
    """دکمهٔ رد کردن فیلد جاری (اختیاری بودن فیلدها)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ رد کردن این فیلد", callback_data="kn_skip_field")],
    ])


def _impact_keyboard() -> InlineKeyboardMarkup:
    """انتخاب دکمه‌ای «تاثیر اجرای پیشنهاد» (کیفی/کمی)."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📈 کیفی", callback_data="kn_impact:کیفی"),
            InlineKeyboardButton("🔢 کمی", callback_data="kn_impact:کمی"),
        ],
    ])


def _skip_title_keyboard() -> InlineKeyboardMarkup:
    """دکمهٔ رد کردن سمت گزارش‌دهنده (اختیاری)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ رد کردن (سمت ندارد)", callback_data="kn_skip:title")],
    ])


def _interview_start_keyboard() -> InlineKeyboardMarkup:
    """شروع یا انصراف از مصاحبه بعد از نمایش framework."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ شروع مصاحبه", callback_data="kn_interview:start")],
        [InlineKeyboardButton("❌ انصراف", callback_data="menu:main")],
    ])


def _interview_continue_keyboard() -> InlineKeyboardMarkup:
    """در هر نوبت مصاحبه، دکمهٔ پایان زودهنگام در دسترس است."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✓ پایان مصاحبه", callback_data="kn_interview:done")],
    ])


def _org_meta_keyboard(knowledge_type: str, current: dict) -> InlineKeyboardMarkup:
    """منوی تنظیمات سازمانی — مطابق فیلدهای فرم رسمی دانا برای هر نوع."""
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            f"🌳 درخت دانش: {current.get('tree_display', 'انتخاب‌نشده')}",
            callback_data="kn_org:tree",
        )],
    ]
    if knowledge_type == "suggestion":
        rows.append([
            InlineKeyboardButton(
                f"👥 کمیته تخصصی: {current.get('committee_display', 'خالی')}",
                callback_data="kn_org:committee",
            )
        ])
        # «بذر پیشنهاد» طبق فرم دانا خالی میماند — پرسیده نمیشود
    # پروژه: درسآموخته ⭐ الزامی، بقیه اختیاری — همیشه نمایش داده می‌شود تا کاربر بتواند پروژه ذکرشده در متن را اصلاح کند
    star = "⭐" if knowledge_type == "lesson" else ""
    rows.append([
        InlineKeyboardButton(
            f"🏗 پروژه{star}: {current.get('project_display', 'خالی')}",
            callback_data="kn_org:project",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            f"🤝 همکاران: {current.get('colleagues_display', 'خالی')}",
            callback_data="kn_org:colleagues",
        ),
        InlineKeyboardButton(
            f"#️⃣ هشتگها: {current.get('hashtags_display', '—')}",
            callback_data="kn_org:hashtags",
        ),
    ])
    if knowledge_type in ("lesson", "explicit"):
        rows.append([
            InlineKeyboardButton(
                f"🏢 محدوده سازمانی: {current.get('scope_display', 'خالی')}",
                callback_data="kn_org:scope",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            "✓ پایان تنظیمات و ادامه",
            callback_data="kn_org:done",
        ),
        InlineKeyboardButton(
            "⏭ فعلاً پر نمیکنم",
            callback_data="kn_org:skip",
        ),
    ])
    return InlineKeyboardMarkup(rows)


def _tree_mode_keyboard(ai_suggestions: list[dict]) -> InlineKeyboardMarkup:
    """کیبورد ابتدای انتخاب درخت دانش."""
    rows: list[list[InlineKeyboardButton]] = []
    if ai_suggestions:
        rows.append([InlineKeyboardButton(
            f"💡 پیشنهادهای AI ({len(ai_suggestions)} مورد)",
            callback_data="kn_tree:ai",
        )])
    rows.append([
        InlineKeyboardButton("🔍 انتخاب دستی از درخت", callback_data="kn_tree:nav"),
    ])
    rows.append([InlineKeyboardButton("⏭ بعداً در DANA", callback_data="kn_tree:skip")])
    return InlineKeyboardMarkup(rows)


def _tree_drill_keyboard(level: int, parent_path: list[str], options: list[str]) -> InlineKeyboardMarkup:
    """کیبورد drill-down در سطوح مختلف."""
    rows: list[list[InlineKeyboardButton]] = []
    pairs: list[InlineKeyboardButton] = []
    for i, name in enumerate(options):
        cb = f"kn_tree:nav:{level + 1}:{i}"
        pairs.append(InlineKeyboardButton(name, callback_data=cb))
        if len(pairs) == 2:
            rows.append(pairs)
            pairs = []
    if pairs:
        rows.append(pairs)
    if level + 1 >= 4 or not options:
        rows.append([InlineKeyboardButton(
            "✓ تأیید این مسیر",
            callback_data="kn_tree:confirm",
        )])
    if parent_path:
        rows.append([InlineKeyboardButton(
            "↩️ بازگشت",
            callback_data="kn_tree:nav:back",
        )])
    rows.append([InlineKeyboardButton("🏠 منو", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def _tree_ai_suggestions_keyboard(suggestions: list[dict]) -> InlineKeyboardMarkup:
    """کیبورد نمایش پیشنهادهای AI."""
    rows: list[list[InlineKeyboardButton]] = []
    for i, s in enumerate(suggestions):
        path_text = " > ".join(s["path"])
        conf = int(s["confidence"] * 100)
        rows.append([InlineKeyboardButton(
            f"✓ {path_text} ({conf}٪)",
            callback_data=f"kn_tree:ai:pick:{i}",
        )])
    rows.append([
        InlineKeyboardButton("↩️ بازگشت", callback_data="kn_tree:nav:back"),
    ])
    rows.append([InlineKeyboardButton("🏠 منو", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def _field_edit_keyboard(fields_keys: list[str], labels: dict[str, str]) -> InlineKeyboardMarkup:
    """منوی انتخاب فیلد برای ویرایش."""
    rows: list[list[InlineKeyboardButton]] = []
    pairs: list[InlineKeyboardButton] = []
    for key in fields_keys:
        label = labels.get(key, key)
        display = label if len(label) <= 30 else label[:27] + "..."
        pairs.append(InlineKeyboardButton(
            f"✏️ {display}",
            callback_data=f"kn_edit:field:{key}",
        ))
        if len(pairs) == 2:
            rows.append(pairs)
            pairs = []
    if pairs:
        rows.append(pairs)
    rows.append([InlineKeyboardButton(
        "↩️ بازگشت به پیش‌نمایش",
        callback_data="kn_edit:back",
    )])
    return InlineKeyboardMarkup(rows)


def _preview_keyboard() -> InlineKeyboardMarkup:
    """سه کلید همزمان در preview."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ ویرایش", callback_data="kn_edit:back")],
        [
            InlineKeyboardButton("📎 ضمیمهٔ عکس/مدرک", callback_data="kn_photos_start"),
        ],
        [InlineKeyboardButton("✅ تأیید و ثبت نهایی", callback_data="kn_finish")],
    ])


def _photos_done_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 پایان عکس‌ها", callback_data="kn_photos_done")]
    ])


def _date_keyboard() -> InlineKeyboardMarkup:
    today = jdatetime.date.today().strftime("%Y/%m/%d")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 انتخاب از تقویم", callback_data="kndate:open")],
        [InlineKeyboardButton(f"📅 امروز ({today})", callback_data="kn_today")],
    ])


def _compute_title(context) -> str:
    """عنوان را از AI، یا در نبود آن از اولین فیلد/شرح می‌سازد."""
    title = context.user_data.get("kn_title")
    if title and str(title).strip():
        return str(title).strip()
    fields = context.user_data.get("kn_fields") or {}
    for key in ("problem", "subject", "description", "current_state", "context"):
        if fields.get(key):
            return str(fields[key]).strip()[:80]
    raw = (context.user_data.get("kn_description") or "").strip()
    return raw[:80]


# ══════════════════════════════════════════════════════════════════════════════
# نقطه ورود — انتخاب روش
# ══════════════════════════════════════════════════════════════════════════════

@require_registration
async def kn_mode_entry(update, context) -> int:
    """
    callback_data: kn:new
    ابتدا بررسی میکند آیا کاربر ثبت ناتمامی دارد (resume)؛ سپس زیرمنوی انتخاب روش.
    """
    try:
        query = update.callback_query
        await query.answer()

        user_id = update.effective_user.id
        pending = models.find_pending_knowledge_by_user(user_id)
        if pending:
            context.user_data["kn_resume_pending_id"] = pending["id"]
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ ادامه ثبت قبلی", callback_data="kn_resume:yes")],
                [InlineKeyboardButton("🆕 شروع ثبت جدید", callback_data="kn_resume:no")],
                [InlineKeyboardButton("🏠 منو", callback_data="menu:main")],
            ])
            await query.edit_message_text(
                f"شما یک ثبت دانش ناتمام دارید (شناسهٔ #{pending['id']}).\n\n"
                "میخواهید ادامه بدهید یا از نو شروع کنید؟\n"
                "(شروع جدید ثبت قبلی را غیرفعال میکند.)",
                reply_markup=kb,
            )
            if query.message:
                track_prompt(context, query.message)
            return KN_MODE_SELECT

        from engine.knowledge_ai import is_ai_enabled
        kb = _mode_select_keyboard(ai_available=is_ai_enabled())
        await query.edit_message_text(
            "📝 *ثبت دانش/تجربه سازمانی*\n\n"
            "دانش لزوماً به پروژه/پیمانکار خاصی وابسته نیست؛ هر تجربه‌ای را میتوانید ثبت کنید.\n\n"
            "کدام روش را ترجیح میدهید؟",
            parse_mode="Markdown",
            reply_markup=kb,
        )
        if query.message:
            track_prompt(context, query.message)
        return KN_MODE_SELECT

    except Exception:
        logger.exception("خطا در kn_mode_entry")
        try:
            if update.callback_query and update.callback_query.message:
                await update.callback_query.message.reply_text("❌ خطایی رخ داد.")
        except Exception:
            pass
        return ConversationHandler.END


async def kn_resume_yes(update, context) -> int:
    """callback_data: kn_resume:yes — ادامه ثبت ناتمام."""
    try:
        query = update.callback_query
        await query.answer()
        pending_id = context.user_data.pop("kn_resume_pending_id", None)
        if not pending_id:
            await query.edit_message_text(
                "⚠️ ثبت ناتمام یافت نشد.",
                reply_markup=main_menu_keyboard(update.effective_user.id),
            )
            return ConversationHandler.END

        entry = models.get_knowledge_entry_by_id(pending_id)
        if not entry:
            await query.edit_message_text(
                "⚠️ رکورد یافت نشد.",
                reply_markup=main_menu_keyboard(update.effective_user.id),
            )
            return ConversationHandler.END

        context.user_data[_KEY_ENTRY_ID] = pending_id
        context.user_data["kn_type"] = entry["knowledge_type"]
        context.user_data["kn_reporter_name"] = entry.get("reporter_name")
        context.user_data["kn_reporter_title"] = entry.get("reporter_title")
        context.user_data["kn_fields"] = entry.get("fields_json") or {}
        if entry.get("raw_description"):
            context.user_data["kn_description"] = entry["raw_description"]
        history = models.get_knowledge_interview_history(pending_id)
        context.user_data["kn_interview_history"] = history
        tree_path = models.get_knowledge_tree_path(pending_id)
        if tree_path:
            context.user_data["kn_tree_path"] = tree_path
        org_md = models.get_knowledge_org_metadata(pending_id)
        if org_md:
            context.user_data["kn_org_metadata"] = org_md

        if history:
            await query.edit_message_text(
                "▶️ ادامه مصاحبهٔ قبلی...\n\n"
                "میتوانید پاسخ بعدی را بفرستید، یا «✓ پایان مصاحبه» بزنید.",
            )
            return KN_INTERVIEW_LOOP

        await query.edit_message_text(
            "▶️ ادامه ثبت دستی قبلی...\n"
            "اگر میخواهید شرح قبلی را تکمیل یا ویرایش کنید، بنویسید.",
        )
        return KN_DESCRIPTION

    except Exception:
        logger.exception("خطا در kn_resume_yes")
        return ConversationHandler.END


async def kn_resume_no(update, context) -> int:
    """callback_data: kn_resume:no — شروع ثبت جدید."""
    try:
        query = update.callback_query
        await query.answer()
        pending_id = context.user_data.pop("kn_resume_pending_id", None)
        if pending_id:
            try:
                models.set_knowledge_inactive(pending_id)
            except Exception:
                logger.exception("خطا در غیرفعال‌سازی ثبت قبلی")

        from engine.knowledge_ai import is_ai_enabled
        kb = _mode_select_keyboard(ai_available=is_ai_enabled())
        await query.edit_message_text(
            "🆕 ثبت جدید شروع شد.\n\nکدام روش را ترجیح میدهید؟",
            parse_mode="Markdown",
            reply_markup=kb,
        )
        return KN_MODE_SELECT

    except Exception:
        logger.exception("خطا در kn_resume_no")
        return ConversationHandler.END


async def kn_mode_manual(update, context) -> int:
    """callback_data: kn_mode:manual — سازگاری با نسخه قدیم → دستیِ خالص"""
    return await kn_mode_direct(update, context)


async def kn_mode_direct(update, context) -> int:
    """callback_data: kn_mode:direct — ثبت مستقیم فرمی بدون AI."""
    try:
        query = update.callback_query
        await query.answer()
        context.user_data["kn_mode"] = "direct"
        await query.edit_message_text(
            "📋 *ثبت مستقیم فرمی (بدون هوش مصنوعی)*\n\n"
            "در این روش تمام فیلدهای فرم دانا را قدم‌به‌قدم از شما می‌پرسم — "
            "بدون نیاز به هوش مصنوعی.\n\n"
            "📚 نوع دانش را انتخاب کنید:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(_type_buttons()),
        )
        return KN_TYPE

    except Exception:
        logger.exception("خطا در kn_mode_direct")
        return ConversationHandler.END


async def kn_mode_interview(update, context) -> int:
    """callback_data: kn_mode:interview — مصاحبه با AI از ابتدا."""
    try:
        query = update.callback_query
        await query.answer()

        from engine.knowledge_ai import is_ai_enabled
        if not is_ai_enabled():
            await query.edit_message_text(
                "⚠️ *هوش مصنوعی در دسترس نیست*\n\n"
                "روش مصاحبه با AI نیاز به اتصال AI دارد "
                "(KNOWLEDGE_AI_MODEL و کلید API تنظیم نشده‌اند).\n\n"
                "میتوانید از روش دستی استفاده کنید.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✍️ رفتن به روش دستی", callback_data="kn_mode:manual")],
                    [InlineKeyboardButton("🏠 منو", callback_data="menu:main")],
                ]),
            )
            return KN_MODE_SELECT

        context.user_data["kn_mode"] = "interview"
        await query.edit_message_text(
            "🎙️ *مصاحبه با AI*\n\n"
            "AI با آگاهی از ساختار استاندارد DANA سؤال میپرسد و اطلاعات را جمع‌آوری میکند.\n\n"
            "🎙️ *راهنما:* میتوانید بهجای تایپ، ویس بفرستید — فارسی صحبت کنید و "
            "صدای شما توسط هوش مصنوعی به متن تبدیل میشود.\n\n"
            "📚 ابتدا نوع دانش را انتخاب کنید:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(_type_buttons()),
        )
        return KN_TYPE

    except Exception:
        logger.exception("خطا در kn_mode_interview")
        return ConversationHandler.END


async def kn_promote_mode(update, context) -> int:
    """
    callback_data: kn:promote:manual | kn:promote:interview
    ارتقای مشاهده به دانش — از obs_promote می‌آید.
    متن مشاهده به عنوان شرح اولیه در kn_description قرار می‌گیرد.
    """
    try:
        query = update.callback_query
        await query.answer()

        # روش را از callback_data می‌خوانیم
        mode = (query.data or "").split(":")[2]  # manual | interview

        # مشاهده در حال ارتقا
        pending_obs = context.user_data.get("kn_observation_pending") or {}
        obs_content = pending_obs.get("content", "")

        context.user_data["kn_mode"] = mode
        context.user_data["kn_from_observation"] = pending_obs.get("obs_id")

        # اگر از مشاهده آمده، شرح اولیه را آماده می‌کنیم
        if obs_content:
            context.user_data["kn_description"] = obs_content

        if mode == "interview":
            from engine.knowledge_ai import is_ai_enabled
            if not is_ai_enabled():
                await query.edit_message_text(
                    "⚠️ *هوش مصنوعی در دسترس نیست*\n\n"
                    "میتوانید از روش دستی استفاده کنید.",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("✍️ رفتن به روش دستی", callback_data="kn_mode:manual")],
                        [InlineKeyboardButton("🏠 منو", callback_data="menu:main")],
                    ]),
                )
                return KN_MODE_SELECT

        if mode == "manual":
            await query.edit_message_text(
                "✍️ *روش دستی — ارتقا از مشاهده*\n\n"
                "مشاهدهٔ شما به عنوان شرح اولیه در نظر گرفته شد.\n"
                "🎙️ *راهنما:* میتوانید بهجای تایپ، ویس بفرستید — فارسی صحبت کنید و "
                "صدای شما توسط هوش مصنوعی به متن تبدیل میشود.\n\n"
                "📚 نوع دانش را انتخاب کنید:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(_type_buttons()),
            )
        else:
            await query.edit_message_text(
                "🎙️ *مصاحبه با AI — ارتقا از مشاهده*\n\n"
                "مشاهدهٔ شما به عنوان شرح اولیه در نظر گرفته شد و AI از شما سؤال میپرسد.\n"
                "🎙️ *راهنما:* میتوانید بهجای تایپ، ویس بفرستید — فارسی صحبت کنید و "
                "صدای شما توسط هوش مصنوعی به متن تبدیل میشود.\n\n"
                "📚 نوع دانش را انتخاب کنید:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(_type_buttons()),
            )
        return KN_TYPE

    except Exception:
        logger.exception("خطا در kn_promote_mode")
        return ConversationHandler.END


async def kn_reporter_name(update, context) -> int:
    try:
        name = _clean_text(_input_text(update, context), _MAX_NAME)
        if name is None:
            await _prompt_reply(update.message, context, "❌ نام نامعتبر است. دوباره وارد کنید:")
            return KN_REPORTER_NAME
        context.user_data["kn_reporter_name"] = name
        await _prompt_reply(
            update.message, context,
            "💼 سمت گزارش‌دهنده را وارد کنید (مثلاً «سرپرست شیفت»)، یا رد کنید:",
            reply_markup=_skip_title_keyboard(),
        )
        return KN_REPORTER_TITLE
    except Exception:
        logger.exception("خطا در kn_reporter_name")
        await update.message.reply_text("❌ خطایی رخ داد.")
        return ConversationHandler.END


async def kn_reporter_title(update, context) -> int:
    value = _clean_text(_input_text(update, context), _MAX_NAME)
    if value is None:
        await _prompt_reply(update.message, context, "❌ سمت نامعتبر است. دوباره وارد کنید:")
        return KN_REPORTER_TITLE
    context.user_data["kn_reporter_title"] = value
    await _prompt_reply(
        update.message, context,
        "📚 نوع دانش را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(_type_buttons()),
    )
    return KN_TYPE


async def kn_reporter_title_skip(update, context) -> int:
    await update.callback_query.answer()
    context.user_data["kn_reporter_title"] = ""
    await update.callback_query.edit_message_text(
        "📚 نوع دانش را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(_type_buttons()),
    )
    return KN_TYPE


async def kn_type(update, context) -> int:
    """callback_data: kn_type:<lesson|suggestion|explicit>"""
    await update.callback_query.answer()
    value = update.callback_query.data.split(":", 1)[1]
    if value not in FIELD_SCHEMAS:
        await update.callback_query.edit_message_text(
            "⚠️ نوع دانش نامعتبر است.", reply_markup=main_menu_keyboard(update.effective_user.id)
        )
        return ConversationHandler.END
    context.user_data["kn_type"] = value
    # پروفایل برای شخصی‌سازی مصاحبه (نام/سمت/پروژه)
    try:
        u = get_user_by_telegram_id(update.effective_user.id) or {}
        context.user_data["kn_user_profile"] = u
        # پیش‌پر کردن نام/سمت اگر هنوز پرسیده نشده
        if not context.user_data.get("kn_reporter_name"):
            context.user_data["kn_reporter_name"] = u.get("full_name") or ""
        if not context.user_data.get("kn_reporter_title"):
            context.user_data["kn_reporter_title"] = u.get("position") or ""
        if u.get("project_name"):
            context.user_data.setdefault("kn_org_metadata", {})["project"] = u.get("project_name")
    except Exception:
        logger.exception("خواندن پروفایل برای مصاحبه ناموفق")

    # مسیر بر اساس روش انتخابی
    if context.user_data.get("kn_mode") == "interview":
        from engine.knowledge_interview import INTERVIEW_FRAMEWORKS
        framework_keys = INTERVIEW_FRAMEWORKS.get(value, [])
        labels = FIELD_SCHEMAS.get(value, {})
        lines = ["📚 چارچوب راهنما — " + TYPE_LABELS[value], ""]
        for i, key in enumerate(framework_keys, start=1):
            lbl = labels.get(key, key)
            lines.append(f"  {i}) {lbl}")
        lines.append("")
        lines.append("برای شروع، دکمهٔ زیر را بزنید.")
        await update.callback_query.edit_message_text(
            "📚 چارچوب راهنما — " + TYPE_LABELS[value] + "\n\n"
            "🎙️ *راهنما:* می‌توانید به‌جای تایپ، ویس بفرستید — فارسی صحبت کنید و "
            "صدای شما توسط هوش مصنوعی به متن تبدیل می‌شود.\n\n"
            + "\n".join(lines[1:]),
            reply_markup=_interview_start_keyboard(),
        )
        return KN_INTERVIEW_FRAMEWORK

    # روش دستیِ خالصِ بدون AI — دقیقاً مطابق فرم دانا
    if context.user_data.get("kn_mode") in ("direct", "manual"):
        from engine.knowledge_ai import MANUAL_DIRECT_QUESTIONS
        # کپی لیست سوالات برای این نوع دانش
        context.user_data["kn_direct_keys"] = [q["key"] for q in MANUAL_DIRECT_QUESTIONS.get(value, [])]
        context.user_data["kn_direct_questions"] = MANUAL_DIRECT_QUESTIONS.get(value, [])
        context.user_data["kn_fields"] = {}
        context.user_data.pop("kn_polished", None)
        # برای درس‌آموخته، حافظهٔ polished_description را هم پاک کن
        if value == "lesson":
            context.user_data.pop("kn_title", None)
        return await _ask_next_direct_field(update.callback_query.message, context)

    # روش دستی قدیم (هیبریدی با AI) — برای سازگاری
    await update.callback_query.edit_message_text(
        f"نوع: *{TYPE_LABELS[value]}*\n\n"
        "✍️ تجربه/دانش خود را آزادانه شرح دهید — هرچه جزئیات بیشتر باشد "
        "استخراج فیلدها دقیق‌تر است.\n"
        "🎙️ *راهنما:* می‌توانید به‌جای تایپ، ویس بفرستید — فارسی صحبت کنید و "
        "صدای شما توسط هوش مصنوعی به متن تبدیل می‌شود.\n"
        f"(حداکثر {_MAX_DESC} کاراکتر)",
        parse_mode="Markdown",
    )
    return KN_DESCRIPTION


# ── دستیِ خالصِ بدون AI — پرسش مستقیم فیلدهای دانا ────────────────────────

async def _ask_next_direct_field(msg, context) -> int:
    """سؤال بعدی دستیِ خالص را می‌پرسد؛ اگر تمام شد به عکس‌ها می‌رود."""
    from engine.knowledge_ai import MANUAL_DIRECT_QUESTIONS

    keys = context.user_data.get("kn_direct_keys", [])
    questions = {q["key"]: q for q in MANUAL_DIRECT_QUESTIONS.get(context.user_data.get("kn_type", ""), [])}

    if not keys:
        return await _open_photos(msg, context)

    curr_key = keys[0]
    q = questions.get(curr_key, {})
    label = q.get("label", curr_key)
    help_text = q.get("help", "")
    required = q.get("required", True)
    is_button = q.get("is_button", False)

    # برای فیلدهای دکمه‌ای (impact_type, subtype) کیبورد مخصوص
    if is_button:
        from engine.knowledge_ai import BUTTON_FIELDS
        k_type = context.user_data.get("kn_type", "")
        opts = BUTTON_FIELDS.get(k_type, {}).get(curr_key, q.get("options", []))
        rows = [[InlineKeyboardButton(opt, callback_data=f"kn_direct:btn:{opt}")] for opt in opts]
        rows.append([InlineKeyboardButton("⏭ رد کردن", callback_data="kn_direct:skip")])
        kb = InlineKeyboardMarkup(rows)
        await _prompt_reply(
            msg, context,
            f"*{label}*{' *' if required else ''}\n\n{help_text}",
            reply_markup=kb,
        )
        return KN_FIELD_DIRECT

    # فیلد متنی
    kb = None
    if not required:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏭ رد کردن", callback_data="kn_direct:skip")]])
    await _prompt_reply(
        msg, context,
        f"*{label}*{' *' if required else ''}\n\n{help_text}\n\nلطفاً پاسخ را بنویسید:",
        reply_markup=kb,
    )
    return KN_FIELD_DIRECT


async def kn_field_direct_input(update, context) -> int:
    """پاسخ متنی به سؤال دستیِ خالص."""
    text = _input_text(update, context).strip()
    if not text:
        await _prompt_reply(update.message, context, "❌ متن خالی است. دوباره بنویسید:")
        return KN_FIELD_DIRECT
    keys = context.user_data.get("kn_direct_keys", [])
    if not keys:
        return await _ask_next_direct_field(update.message, context)

    curr_key = keys.pop(0)
    context.user_data["kn_direct_keys"] = keys

    # ذخیره بر اساس نوع کلید
    if curr_key == "kn_title":
        context.user_data["kn_title"] = text
    elif curr_key == "lesson_description":
        # شرح درس‌آموخته — به‌صورت polished_description ذخیره تا build_report استفاده کند
        context.user_data.setdefault("kn_polished", {})["polished_description"] = text
        # همچنین برای سازگاری در fields هم بگذار
        context.user_data.setdefault("kn_fields", {})["lesson"] = text[:500]
    elif curr_key in ("impact_type", "subtype"):
        # این‌ها نباید به‌صورت متنی بیایند — دکمه‌ای هستند
        pass
    else:
        context.user_data.setdefault("kn_fields", {})[curr_key] = text

    return await _ask_next_direct_field(update.message, context)


async def kn_field_direct_skip(update, context) -> int:
    """رد کردن فیلد اختیاری دستیِ خالص."""
    await update.callback_query.answer()
    keys = context.user_data.get("kn_direct_keys", [])
    if keys:
        keys.pop(0)
        context.user_data["kn_direct_keys"] = keys
    return await _ask_next_direct_field(update.callback_query.message, context)


async def kn_direct_btn_choice(update, context) -> int:
    """انتخاب دکمه‌ای در دستیِ خالص (impact_type / subtype)."""
    await update.callback_query.answer()
    value = update.callback_query.data.split(":", 2)[2]
    keys = context.user_data.get("kn_direct_keys", [])
    if keys:
        curr_key = keys.pop(0)
        context.user_data["kn_direct_keys"] = keys
        if curr_key == "impact_type":
            context.user_data["kn_impact_type"] = value
        elif curr_key == "subtype":
            context.user_data.setdefault("kn_fields", {})["subtype"] = value
        else:
            context.user_data.setdefault("kn_fields", {})[curr_key] = value
    return await _ask_next_direct_field(update.callback_query.message, context)


@_busy_ai
async def kn_description(update, context) -> int:
    """شرح آزاد ← استخراج با AI ← [تأیید طبقه‌بندی] ← پرسش فیلدهای ناقص."""
    desc = _clean_text(_input_text(update, context), _MAX_DESC)
    if desc is None:
        await _prompt_reply(
            update.message, context,
            f"❌ شرح نامعتبر است (حداکثر {_MAX_DESC} کاراکتر). دوباره بنویسید:",
        )
        return KN_DESCRIPTION
    context.user_data["kn_description"] = desc

    await _prompt_reply(update.message, context, "🔄 در حال استخراج فیلدها با هوش مصنوعی...")

    knowledge_type = context.user_data.get("kn_type")
    result = await extract_fields(knowledge_type, desc)

    context.user_data["kn_title"] = result["title"]
    context.user_data["kn_hashtags"] = result["hashtags"] or []
    context.user_data["kn_fields"] = dict(result["fields"])
    context.user_data["kn_impact_type"] = result["impact_type"]
    context.user_data["kn_missing"] = list(result["missing"])

    labels = FIELD_SCHEMAS.get(knowledge_type, {})
    summary: list[str] = []
    if result["fields"]:
        summary.append("🤖 *فیلدهای استخراج‌شده توسط هوش مصنوعی:*")
        if result["title"]:
            summary.append(f"🔖 *عنوان*: {result['title']}")
        for key, value in result["fields"].items():
            summary.append(f"▫️ *{labels.get(key, key)}*: {value}")
    if result["missing"]:
        summary.append("")
        summary.append("✍️ فیلدهای زیر در شرح مشخص نبودند — یک‌به‌یک پاسخ دهید:")
    if summary:
        await _prompt_reply(update.message, context, "\n".join(summary))

    # پیشنهاد طبقه‌بندی AI — اگر با انتخاب اپراتور تعارض دارد، تأیید بگیر
    classification = result["classification"]
    if classification.get("conflict"):
        recommended = classification.get("recommended", "ambiguous")
        reason = classification.get("reason") or ""
        current_label = TYPE_LABELS.get(knowledge_type, knowledge_type)
        if recommended == "ambiguous":
            msg = (
                "🤖 هوش مصنوعی نتوانست نوع دانش را قطعی تشخیص دهد، اما شما "
                f"«{current_label}» را انتخاب کرده‌اید؛ با همان ادامه می‌دهیم."
            )
        else:
            msg = (
                "⚠️ *پیشنهاد طبقه‌بندی*: هوش مصنوعی بر اساس محتوای متن، این تجربه را بیشتر "
                f"«{TYPE_LABELS[recommended]}» تشخیص داد"
                + (f" ({reason})" if reason else "")
                + ".\n\n"
                f"شما «{current_label}» را انتخاب کرده بودید. کدام را ثبت کنیم؟"
            )
        await _prompt_reply(update.message, context, msg,
                            reply_markup=_type_conflict_keyboard(recommended))
        return KN_TYPE_CONFIRM

    return await _continue_after_extraction(update.message, context)


async def _continue_after_extraction(msg, context) -> int:
    """بعد از استخراج (و تأیید نوع): پرسش فیلدهای ناقص یا رفتن به عکس‌ها."""
    missing = context.user_data.get("kn_missing") or []
    if missing:
        return await _ask_next_field(msg, context)
    return await _open_photos(msg, context)


async def kn_type_keep(update, context) -> int:
    """callback_data: kn_type_keep — نگه‌داشتن نوع انتخابی با وجود پیشنهاد AI."""
    try:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(
            f"✓ نوع «{TYPE_LABELS.get(context.user_data.get('kn_type'), '')}» حفظ شد. ادامه..."
        )
        return await _continue_after_extraction(query.message, context)
    except Exception:
        logger.exception("خطا در kn_type_keep")
        return ConversationHandler.END


@_busy_ai
async def kn_type_switch(update, context) -> int:
    """callback_data: kn_type_switch:<type> — تغییر نوع بر اساس پیشنهاد AI و استخراج مجدد."""
    try:
        query = update.callback_query
        await query.answer()

        new_type = query.data.split(":", 1)[1]
        if new_type not in FIELD_SCHEMAS:
            return ConversationHandler.END

        context.user_data["kn_type"] = new_type
        desc = context.user_data.get("kn_description")
        await query.edit_message_text("🔄 در حال استخراج مجدد فیلدها با هوش مصنوعی...")

        result = await extract_fields(new_type, desc)

        context.user_data["kn_title"] = result["title"]
        context.user_data["kn_hashtags"] = result["hashtags"] or []
        context.user_data["kn_fields"] = dict(result["fields"])
        context.user_data["kn_impact_type"] = result["impact_type"]
        context.user_data["kn_missing"] = list(result["missing"])

        labels = FIELD_SCHEMAS.get(new_type, {})
        summary = [f"📚 نوع جدید: *{TYPE_LABELS[new_type]}*"]
        if result["fields"]:
            summary.append("🤖 فیلدهای استخراج‌شده:")
            if result["title"]:
                summary.append(f"🔖 *عنوان*: {result['title']}")
            for key, value in result["fields"].items():
                summary.append(f"▫️ *{labels.get(key, key)}*: {value}")
        if result["missing"]:
            summary.append("")
            summary.append("✍️ فیلدهای مشخص‌نشده یک‌به‌یک پرسیده می‌شوند:")
        await _prompt_reply(query.message, context, "\n".join(summary))

        return await _continue_after_extraction(query.message, context)
    except Exception:
        logger.exception("خطا در kn_type_switch")
        return ConversationHandler.END


async def kn_impact(update, context) -> int:
    """callback_data: kn_impact:<کیفی|کمی> — تاثیر اجرای پیشنهاد."""
    try:
        query = update.callback_query
        await query.answer()
        value = query.data.split(":", 1)[1]
        context.user_data["kn_impact_type"] = value
        return await _ask_next_field(query.message, context)
    except Exception:
        logger.exception("خطا در kn_impact")
        return ConversationHandler.END


async def _ask_next_field(msg, context) -> int:
    """فیلد ناقص بعدی را می‌پرسد؛ اگر همه پر شدند به عکس‌ها می‌رود."""
    missing = context.user_data.get("kn_missing") or []
    if not missing:
        return await _open_photos(msg, context)

    key = missing.pop(0)
    context.user_data["kn_missing"] = missing
    context.user_data["kn_current_field"] = key

    # «تاثیر اجرای پیشنهاد» یک انتخاب دوگانه است نه متن آزاد
    if key == "impact_type":
        await _prompt_reply(
            msg, context,
            "📊 *تاثیر اجرای پیشنهاد* چیست؟\n"
            "«کیفی» = اثر بدون رقم مشخص | «کمی» = اثر با عدد/درصد/مبلغ",
            reply_markup=_impact_keyboard(),
        )
        return KN_FIELD_ANSWER

    knowledge_type = context.user_data.get("kn_type")
    label = FIELD_SCHEMAS.get(knowledge_type, {}).get(key, key)
    await _prompt_reply(
        msg, context,
        f"✍️ *{label}* را وارد کنید:",
        reply_markup=_field_skip_keyboard(),
    )
    return KN_FIELD_ANSWER


async def kn_field_answer(update, context) -> int:
    """پاسخ به فیلد ناقص جاری (متن)."""
    try:
        value = _clean_text(_input_text(update, context), _MAX_FIELD)
        if value is None:
            await _prompt_reply(
                update.message, context,
                f"❌ پاسخ نامعتبر است (حداکثر {_MAX_FIELD} کاراکتر). دوباره بنویسید:",
            )
            return KN_FIELD_ANSWER
        key = context.user_data.get("kn_current_field")
        if key:
            context.user_data.setdefault("kn_fields", {})[key] = value
        return await _ask_next_field(update.message, context)
    except Exception:
        logger.exception("خطا در kn_field_answer")
        await update.message.reply_text("❌ خطایی رخ داد.")
        return ConversationHandler.END


async def kn_field_skip(update, context) -> int:
    """callback_data: kn_skip_field — رد کردن فیلد ناقص جاری."""
    try:
        query = update.callback_query
        await query.answer()
        return await _ask_next_field(query.message, context)
    except Exception:
        logger.exception("خطا در kn_field_skip")
        return ConversationHandler.END


async def _open_photos(msg, context) -> int:
    """مرحلهٔ عکس‌ها (اختیاری) — برای دانش صریح پیام ویژه دارد."""
    photos = context.user_data.setdefault("kn_photos", [])
    count_text = f" ({len(photos)} عکس ثبت شد)" if photos else ""
    k_type = context.user_data.get("kn_type")
    if k_type == "explicit":
        prompt = (
            "📎 *عکس، متن، PDF یا صدای خود را بارگذاری کنید*\n\n"
            "فایل دانش صریح خود را بفرستید (اختیاری — می‌توانید چند فایل بفرستید).\n"
            f"بعد از اتمام، «پایان عکس‌ها» را بزنید.{count_text}"
        )
    else:
        prompt = (
            "📸 عکس‌های مرتبط با تجربه را بفرستید (اختیاری، چندتایی هم می‌توانید).\n"
            f"بعد از اتمام، دکمهٔ «پایان عکس‌ها» را بزنید.{count_text}"
        )
    await _prompt_reply(
        msg, context,
        prompt,
        reply_markup=_photos_done_keyboard(),
    )
    context.user_data["kn_attach_cleaned"] = False
    return KN_PHOTOS


def _choose_photo(photos):
    """انتخاب سایز نزدیک به ~800px (معادل سایز متوسط تلگرام — صرفه‌جویی فضا)."""
    if len(photos) >= 3:
        return sorted(photos, key=lambda p: p.width or 0)[-2]
    return photos[-1]


# تجمیع پیام تأیید عکس‌ها — کاربر چند عکس پشت‌سرهم بفرستد فقط یک پیام خلاصه می‌رود
_KN_PHOTO_FLUSH_DELAY = 2.5
_kn_photo_pending: dict[int, dict] = {}  # user_id -> {"count": n, "_task": Task}


async def _flush_kn_photo_notice(msg, context, user_id: int) -> None:
    """پس از مکث کوتاه، یک پیام جمعی برای عکس‌های تازه ذخیره‌شده می‌فرستد."""
    try:
        await asyncio.sleep(_KN_PHOTO_FLUSH_DELAY)
    except asyncio.CancelledError:
        return
    slot = _kn_photo_pending.pop(user_id, None)
    if not slot:
        return
    count = slot.get("count", 0)
    if count <= 0:
        return
    total = len(context.user_data.get("kn_photos", []))
    # خلاصهٔ قبلی عکس‌ها حذف و جدید جایگزین شود
    await delete_tracked(context, "_attach_summary")
    try:
        sent = await msg.reply_text(
            f"✅ {count} عکس جدید ذخیره شد (مجموع: {total}).\n"
            "اگر عکس دیگری هست بفرستید، وگرنه «پایان عکس‌ها» را بزنید.",
            reply_markup=_photos_done_keyboard(),
        )
        track_prompt(context, sent, "_attach_summary")
    except Exception:
        logger.exception("خطا در ارسال پیام جمعی عکس‌های دانش")


async def kn_photo_received(update, context) -> int:
    """عکس‌ها در پوشهٔ pending نگه داشته و هنگام ثبت نهایی منتقل می‌شوند."""
    try:
        # سایز متوسط (~800px) به‌جای بزرگترین
        chosen = _choose_photo(update.message.photo)
        file = chosen.file_id
        data = await context.bot.get_file(file)

        user_dir = os.path.join(config.KN_PHOTO_PATH, "pending", str(update.effective_user.id))
        os.makedirs(user_dir, exist_ok=True)
        num = len(context.user_data.get("kn_photos", [])) + 1
        full_path = os.path.join(user_dir, f"{num:03d}.jpg")
        with open(full_path, "wb") as fh:
            fh.write(data)

        photos_list = context.user_data.setdefault("kn_photos", [])
        photos_list.append(full_path)

        # اولین عکس: پرامپت مرحلهٔ عکس‌ها حذف شود
        if not context.user_data.get("kn_attach_cleaned"):
            await delete_tracked(context)
            context.user_data["kn_attach_cleaned"] = True

        # پیام تأیید فوری نمی‌فرستیم — با مکث کوتاه تجمیع می‌شود
        uid = update.effective_user.id if update.effective_user else 0
        slot = _kn_photo_pending.setdefault(uid, {})
        slot["count"] = slot.get("count", 0) + 1
        old_task = slot.get("_task")
        if old_task:
            old_task.cancel()
        slot["_task"] = asyncio.create_task(
            _flush_kn_photo_notice(update.message, context, uid)
        )
        return KN_PHOTOS
    except Exception:
        logger.exception("خطا در ذخیره عکس دانش")
        await update.message.reply_text("❌ ذخیره عکس ناموفق بود. دوباره تلاش کنید:")
        return KN_PHOTOS


async def kn_photos_done(update, context) -> int:
    """callback_data: kn_photos_done — پایان دریافت عکس."""
    try:
        await update.callback_query.answer()
        # اگر در فاز پیش‌نمایش عکس اضافه شده، مستقیم به پیش‌نمایش برگرد
        if context.user_data.get("kn_report"):
            return await _save_and_preview(update.callback_query.message, context)
        await update.callback_query.edit_message_text(
            f"🗓️ تاریخ ثبت را وارد کنید (فرمت {jdatetime.date.today().strftime('%Y/%m/%d')} سال/ماه/روز).\n"
            "برای امروز دکمهٔ زیر را بزنید:",
            reply_markup=_date_keyboard(),
        )
        return KN_DATE
    except Exception:
        logger.exception("خطا در kn_photos_done")
        return ConversationHandler.END


async def _go_to_org_menu(update, context) -> int:
    """پس از تعیین تاریخ: ساخت رکورد (در صورت نبود) و ورود به تنظیمات سازمانی."""
    chat_id = update.effective_chat.id
    user = get_user_by_telegram_id(chat_id) if chat_id else None
    if user is None:
        await _prompt_reply(
            getattr(update, "message", None) or (update.callback_query.message if update.callback_query else None),
            context,
            "⛔ حساب شما ثبت نشده است. لطفاً /start بزنید.",
        )
        return ConversationHandler.END
    _ensure_knowledge_entry(context, chat_id, user)
    return await _show_org_menu(update, context)


async def kn_date(update, context) -> int:
    value = _clean_text(update.message.text, 30)
    if not value:
        await _prompt_reply(update.message, context, "❌ تاریخ خالی است. دوباره وارد کنید:")
        return KN_DATE
    ok, _err = validate_jalali_date_str(value)
    if not ok:
        await _prompt_reply(update.message, context, "❌ تاریخ نامعتبر است. مثال: ۱۴۰۴/۰۵/۱۲")
        return KN_DATE
    normalized = value.replace("-", "/")
    context.user_data["kn_date"] = normalized
    return await _go_to_org_menu(update, context)


async def kn_date_today(update, context) -> int:
    """callback_data: kn_today"""
    await update.callback_query.answer()
    context.user_data["kn_date"] = jdatetime.date.today().strftime("%Y/%m/%d")
    return await _go_to_org_menu(update, context)


# ── تقویم جلالی ──

_KN_CAL_CAPTION = "📅 *انتخاب تاریخ ثبت* — روز موردنظر را بزنید:"


async def kn_cal_open(update, context) -> int:
    """باز کردن تقویم برای انتخاب تاریخ ثبت دانش."""
    await update.callback_query.answer()
    today = jdatetime.date.today()
    context.user_data["kn_cal_view"] = [today.year, today.month]
    await update.callback_query.edit_message_text(
        _KN_CAL_CAPTION,
        parse_mode="Markdown",
        reply_markup=build_calendar_keyboard("kndate", today.year, today.month),
    )
    return KN_DATE


async def kn_cal_nav(update, context) -> int:
    """ناوبری ماه/سال در تقویم."""
    await update.callback_query.answer()
    parsed = parse_view_data(update.callback_query.data or "")
    if not parsed:
        return KN_DATE
    year, month = parsed
    context.user_data["kn_cal_view"] = [year, month]
    await update.callback_query.edit_message_text(
        _KN_CAL_CAPTION,
        parse_mode="Markdown",
        reply_markup=build_calendar_keyboard("kndate", year, month),
    )
    return KN_DATE


async def kn_cal_none(update, context) -> int:
    """کلیک روی سلول غیرفعال — هیچ کاری نکن."""
    try:
        await update.callback_query.answer()
    except Exception:
        pass
    return KN_DATE


async def kn_cal_pick(update, context) -> int:
    """انتخاب روز از تقویم — همان مسیر ورودی متنی/امروز."""
    await update.callback_query.answer()
    parsed = parse_pick_data(update.callback_query.data or "")
    if not parsed:
        return KN_DATE
    year, month, day = parsed
    context.user_data["kn_date"] = f"{year}/{month:02d}/{day:02d}"
    return await _go_to_org_menu(update, context)


def _ensure_knowledge_entry(context, chat_id: int | None, user: dict | None = None) -> int:
    """رکورد draft را در صورت نبود میسازد و id آن را برمیگرداند."""
    kid = context.user_data.get(_KEY_ENTRY_ID)
    if kid:
        return kid
    user_row = user or (get_user_by_telegram_id(chat_id) if chat_id else None)
    kid = models.add_knowledge_entry(
        telegram_id=(user_row or {}).get("telegram_id", chat_id or 0),
        knowledge_type=context.user_data.get("kn_type"),
        reporter_name=context.user_data.get("kn_reporter_name") or "—",
        reporter_title=context.user_data.get("kn_reporter_title") or None,
        raw_description=context.user_data.get("kn_description"),
        fields=context.user_data.get("kn_fields") or {},
        reported_date=context.user_data.get("kn_date") or None,
    )
    context.user_data[_KEY_ENTRY_ID] = kid
    return kid


def _gather_report_inputs(context) -> dict:
    """
    ورودی‌های سازمانی/Polish/QA گزارش از context — با fallback به DB.
    (شبکهٔ ایمنی: اگر state حافظه گم شده باشد، از رکورد خوانده میشود.)
    """
    kid = context.user_data.get(_KEY_ENTRY_ID)

    tree_path = context.user_data.get("kn_tree_path")
    if not tree_path and kid:
        try:
            tree_path = models.get_knowledge_tree_path(kid)
        except Exception:
            logger.exception("خواندن tree_path ناموفق")
        if tree_path:
            context.user_data["kn_tree_path"] = tree_path

    org = context.user_data.get("kn_org_metadata") or {}
    if not org and kid:
        try:
            org = models.get_knowledge_org_metadata(kid) or {}
        except Exception:
            logger.exception("خواندن org_metadata ناموفق")
        if org:
            context.user_data["kn_org_metadata"] = org

    return {
        "tree_path": tree_path,
        "org_metadata": org,
        "project_name": (org or {}).get("project"),
        "polished": context.user_data.get("kn_polished"),
        "qa_notes": context.user_data.get("kn_qa_flags"),
    }


def _build_draft(context) -> tuple[dict, str]:
    """گزارش + متن پیش‌نویس DANA را از وضعیت جاری میسازد."""
    inputs = _gather_report_inputs(context)
    report = build_report(
        knowledge_type=context.user_data.get("kn_type"),
        title=_compute_title(context),
        fields=context.user_data.get("kn_fields") or {},
        hashtags=context.user_data.get("kn_hashtags") or None,
        impact_type=context.user_data.get("kn_impact_type"),
        reporter_name=context.user_data.get("kn_reporter_name") or "—",
        reporter_title=context.user_data.get("kn_reporter_title") or None,
        reported_date=context.user_data.get("kn_date") or "—",
        kn_number=None,
        raw_description=context.user_data.get("kn_description"),
        attachments=[f"{i:03d}.jpg" for i in range(1, len(context.user_data.get("kn_photos") or []) + 1)],
        **inputs,
    )
    return report, render_text(report)


async def _save_and_preview(msg, context) -> int:
    """
    ذخیرهٔ پیش‌نویس (draft) در DB —
    عکس‌ها به پوشهٔ نهایی منتقل میشوند و پیش‌نمایش DANA نشان داده میشود.
    """
    try:
        # پرامپت قبلی (تاریخ/تقویم/عکس‌ها) و خلاصهٔ پیوست‌ها حذف شوند
        await delete_tracked(context)
        await delete_tracked(context, "_attach_summary")

        chat_id = msg.chat_id if msg.chat else None

        user = get_user_by_telegram_id(chat_id) if chat_id else None
        if user is None:
            await msg.reply_text("⛔ حساب شما ثبت نشده است. لطفاً /start بزنید.")
            return ConversationHandler.END

        knowledge_id = _ensure_knowledge_entry(context, chat_id, user)
        report, draft = _build_draft(context)
        context.user_data["kn_report"] = report
        try:
            models.set_knowledge_fields(
                knowledge_id, context.user_data.get("kn_fields") or {}, draft
            )
        except Exception:
            logger.exception("ذخیرهٔ draft ناموفق")

        # انتقال عکس‌ها از پوشهٔ موقت به پوشهٔ نهایی + ثبت در DB
        photos = context.user_data.get("kn_photos") or []
        if photos:
            final_dir = os.path.join(config.KN_PHOTO_PATH, str(knowledge_id))
            os.makedirs(final_dir, exist_ok=True)
            for i, src in enumerate(photos, start=1):
                if not os.path.isfile(src):
                    continue
                dest = os.path.join(final_dir, f"{i:03d}.jpg")
                try:
                    os.replace(src, dest)
                    add_knowledge_photo(
                        knowledge_id,
                        os.path.join("media", "kn_photos", str(knowledge_id), f"{i:03d}.jpg"),
                    )
                except OSError:
                    logger.exception("انتقال عکس دانش ناموفق: %s", src)
            context.user_data["kn_photos"] = []

        text = "📋 *پیش‌نمایش پیش‌نویس DANA*\n\n" + draft
        try:
            sent = await msg.reply_text(text, parse_mode="Markdown", reply_markup=_preview_keyboard())
        except Exception as e:
            logger.warning("ارسال پیش‌نمایش با Markdown ناموفق (%s) — تلاش با متن کوتاه", e)
            # Fallback: متن کوتاه بدون Markdown (framework خودش Markdown را حذف می‌کند)
            short = text[:3500]
            if len(text) > 3500:
                short += "\n\n… (ادامه در فایل‌های پیوست)"
            try:
                sent = await msg.reply_text(short, reply_markup=_preview_keyboard())
            except Exception:
                logger.exception("ارسال پیش‌نمایش کوتاه هم ناموفق")
                sent = await msg.reply_text(
                    "📋 پیش‌نمایش آماده است — برای ادامه «تأیید و ثبت نهایی» را بزنید.",
                    reply_markup=_preview_keyboard(),
                )
        track_prompt(context, sent)
        return KN_PREVIEW

    except Exception:
        logger.exception("خطا در _save_and_preview دانش")
        try:
            await msg.reply_text("❌ خطایی رخ داد.")
        except Exception:
            pass
        return ConversationHandler.END


async def kn_finish(update, context) -> int:
    """
    callback_data: kn_finish — ثبت نهایی:
    تولید شماره KN + بازسازی پیش‌نویس با شماره + ساخت فایل‌های PDF/DOCX
    + submit + ارسال پیش‌نویس نهایی و فایل‌ها به اپراتور.
    """
    try:
        query = update.callback_query
        await query.answer()

        knowledge_id = context.user_data.get(_KEY_ENTRY_ID)
        if not knowledge_id:
            await query.edit_message_text("⚠️ رکورد یافت نشد.", reply_markup=main_menu_keyboard(update.effective_user.id))
            return ConversationHandler.END

        entry = get_knowledge_entry_by_id(knowledge_id)
        if not entry:
            await query.edit_message_text("⚠️ رکورد یافت نشد.", reply_markup=main_menu_keyboard(update.effective_user.id))
            return ConversationHandler.END

        from engine.knowledge_numbering import generate_knowledge_number

        knowledge_type = context.user_data.get("kn_type") or entry.get("knowledge_type")
        context.user_data["kn_type"] = knowledge_type

        # ⭐ پروژه برای درسآموخته الزامی است (فرم رسمی دانا)
        org = (_gather_report_inputs(context).get("org_metadata") or {})
        if knowledge_type == "lesson" and not str(org.get("project") or "").strip():
            try:
                await query.edit_message_text(
                    "⚠️ فیلد «پروژه» برای ثبت درس‌آموخته الزامی است.\n"
                    "لطفاً ابتدا آن را تعیین کنید:",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🏗 تعیین پروژه", callback_data="kn_org:project")],
                        [InlineKeyboardButton("↩️ بازگشت به پیش‌نمایش", callback_data="kn_edit:back")],
                    ]),
                )
            except Exception:
                pass
            return KN_ORG_META

        number = generate_knowledge_number(entry["project_id"])

        # شبکهٔ ایمنی: گزارش همیشه از وضعیت جاری + DB بازسازی میشود
        # (نه از گزارشِ کششده در حافظه — تا هیچ انتخابی مثل درخت گم نشود)
        report, _draft_fresh = _build_draft(context)
        report = dict(report)
        report["metadata"] = [
            (label, value if label != "شماره ثبت" else number)
            for label, value in report["metadata"]
        ]
        draft = render_text(report)

        fields = entry.get("fields_json") or {}
        set_knowledge_fields(knowledge_id, fields, draft)

        # ساخت فایل‌های PDF و DOCX
        pdf_path: str | None = None
        docx_path: str | None = None
        out_dir = os.path.join(config.KN_OUTPUT_PATH, str(knowledge_id))
        os.makedirs(out_dir, exist_ok=True)
        try:
            pdf_path = os.path.join(out_dir, f"{number}.pdf")
            if not render_dana_pdf(report, pdf_path):
                pdf_path = None
        except Exception:
            logger.exception("ساخت PDF دانش ناموفق (فایل نادیده گرفته شد)")
            pdf_path = None
        try:
            docx_path = os.path.join(out_dir, f"{number}.docx")
            render_dana_docx(report, docx_path)
        except Exception:
            logger.exception("ساخت DOCX دانش ناموفق (فایل نادیده گرفته شد)")
            docx_path = None

        submit_knowledge_entry(knowledge_id, number, pdf_path=pdf_path, docx_path=docx_path)

        # پاک‌سازی داده‌های جلسه
        for key in list(context.user_data):
            if key.startswith("kn_") or key.startswith("_KEY_"):
                context.user_data.pop(key, None)

        final_text = (
            "✅ *دانش با موفقیت ثبت شد*\n\n"
            f"🔢 شمارهٔ ثبت: {number}\n\n"
            "📄 *پیش‌نویس DANA:*\n\n" + draft
        )
        kb = main_menu_keyboard(update.effective_user.id)
        await query.edit_message_text(final_text, parse_mode="Markdown", reply_markup=kb)

        # ارسال فایل‌های خروجی (PDF و سپس DOCX) برای ثبت در DANA
        chat_id = update.effective_chat.id
        if pdf_path and os.path.isfile(pdf_path):
            try:
                with open(pdf_path, "rb") as fh:
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=InputFile(fh.read(), file_name=f"{number}.pdf"),
                        caption=f"📄 پیش‌نویس DANA (PDF) — {number}",
                    )
            except Exception:
                logger.exception("ارسال PDF دانش ناموفق")
        if docx_path and os.path.isfile(docx_path):
            try:
                with open(docx_path, "rb") as fh:
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=InputFile(fh.read(), file_name=f"{number}.docx"),
                        caption=f"📝 پیش‌نویس DANA (Word) — {number}",
                    )
            except Exception:
                logger.exception("ارسال DOCX دانش ناموفق")

        return ConversationHandler.END

    except Exception:
        logger.exception("خطا در kn_finish")
        try:
            await update.callback_query.answer("❌ خطایی رخ داد.", show_alert=True)
        except Exception:
            pass
        return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
# مصاحبه با AI (روش۲)
# ══════════════════════════════════════════════════════════════════════════════

def _create_interview_entry(context, user: dict) -> int:
    """رکورد draft جدید برای مصاحبه میسازد (id را در context ذخیره میکند)."""
    kid = models.add_knowledge_entry(
        telegram_id=user["telegram_id"],
        knowledge_type=context.user_data["kn_type"],
        reporter_name=context.user_data.get("kn_reporter_name") or "—",
        reporter_title=context.user_data.get("kn_reporter_title") or None,
    )
    context.user_data[_KEY_ENTRY_ID] = kid
    return kid


async def _ask_first_interview_question(
    context,
    query_or_msg,
) -> None:
    """اولین سؤال مصاحبه را از AI میگیرد و نمایش میدهد."""
    from engine.knowledge_interview import interview_next_turn

    history = context.user_data.setdefault("kn_interview_history", [])
    # پروفایل برای شخصی‌سازی پرامپت (نام/سمت/پروژه)
    prof = context.user_data.get("kn_user_profile")
    if not prof:
        # fallback از داده‌های گزارش‌دهنده
        prof = {
            "full_name": context.user_data.get("kn_reporter_name"),
            "position": context.user_data.get("kn_reporter_title"),
            "project_name": (context.user_data.get("kn_org_metadata") or {}).get("project"),
        }
    # اولین نوبت: پیام کاربر خالی/آغازین تا AI سؤال اول را بدهد
    result = await interview_next_turn(
        context.user_data["kn_type"], history, "(شروع مصاحبه)", prof
    )

    if result.get("error") == "ai_disabled":
        await query_or_msg.edit_message_text("⚠️ AI در دسترس نیست.")
        return

    ask = result.get("ask") or "تجربه‌تان را تعریف کنید."
    # ثبت سؤال اول AI در تاریخچه
    history.append({"role": "assistant", "content": ask})
    await query_or_msg.edit_message_text(
        f"🤖 *AI:* {ask}\n\n"
        "(پاسخ خود را بنویسید. اگر سؤال نامربوط بود، از «✓ پایان مصاحبه» استفاده کنید.)",
        parse_mode="Markdown",
        reply_markup=_interview_continue_keyboard(),
    )


@_busy_ai
async def kn_interview_start(update, context) -> int:
    """callback_data: kn_interview:start — شروع مصاحبه + اولین سؤال AI."""
    try:
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        user = get_user_by_telegram_id(user_id) if user_id else None
        if user is None:
            await query.edit_message_text("⛔ حساب شما ثبت نشده است. لطفاً /start بزنید.")
            return ConversationHandler.END

        # ساخت رکورد draft برای ثبت تاریخچه
        if not context.user_data.get(_KEY_ENTRY_ID):
            _create_interview_entry(context, user)
        # پاکسازی fields/title/history (اگر resume شده)
        context.user_data.setdefault("kn_fields", {})
        context.user_data.setdefault("kn_interview_history", [])

        await query.edit_message_text("🔄 در حال شروع مصاحبه...")
        await _ask_first_interview_question(context, query)
        return KN_INTERVIEW_LOOP
    except Exception:
        logger.exception("خطا در kn_interview_start")
        return ConversationHandler.END


@_busy_ai
async def kn_interview_loop_text(update, context) -> int:
    """دریافت پیام متنی در حلقهٔ مصاحبه."""
    from engine.knowledge_interview import interview_next_turn

    try:
        user_text = _input_text(update, context).strip()
        if not user_text:
            await _prompt_reply(update.message, context, "❌ متن خالی است. پاسخ خود را بنویسید:")
            return KN_INTERVIEW_LOOP

        knowledge_type = context.user_data["kn_type"]
        history = context.user_data.setdefault("kn_interview_history", [])

        # ثبت پاسخ کاربر در تاریخچه
        history.append({"role": "user", "content": user_text})

        # پروفایل برای پرامپت شخصی‌سازی
        prof = context.user_data.get("kn_user_profile")
        if not prof:
            prof = {
                "full_name": context.user_data.get("kn_reporter_name"),
                "position": context.user_data.get("kn_reporter_title"),
                "project_name": (context.user_data.get("kn_org_metadata") or {}).get("project"),
            }

        # فراخوانی AI
        result = await interview_next_turn(knowledge_type, history, user_text, prof)

        kid = context.user_data.get(_KEY_ENTRY_ID)

        # اگر AI تشخیص داد نوع دانش باید عوض شود — به کاربر اطلاع بده و DB را هم به‌روز کن
        if result.get("switch_to_type") and result["switch_to_type"] in ("lesson", "suggestion", "explicit") and result["switch_to_type"] != knowledge_type:
            from engine.knowledge_ai import TYPE_LABELS as _TL
            old_label = _TL.get(knowledge_type, knowledge_type)
            new_type = result["switch_to_type"]
            new_label = _TL.get(new_type, new_type)
            context.user_data["kn_type"] = new_type
            knowledge_type = new_type
            if kid:
                try:
                    models.update_knowledge_type(kid, new_type)
                except Exception:
                    logger.exception("به‌روزرسانی knowledge_type ناموفق")
            try:
                await _prompt_reply(
                    update.message, context,
                    f"⚠️ با توجه به توضیحات شما، این مورد بیشتر به *{new_label}* شباهت دارد تا *{old_label}*.\n"
                    f"نوع دانش به *{new_label}* تغییر کرد و ادامه مصاحبه بر اساس همان پیش می‌رود.",
                )
            except Exception:
                logger.exception("ارسال پیام تغییر نوع ناموفق")

        # ثبت تاریخچه + فیلدها در DB برای resume
        if kid:
            try:
                models.set_knowledge_interview_history(kid, history)
            except Exception:
                logger.exception("ذخیرهٔ interview_history ناموفق")

        if result.get("done"):
            # پایان مصاحبه توسط AI — استفاده از fields نهایی
            final_fields = result.get("fields") or context.user_data.get("kn_fields") or {}
            context.user_data["kn_fields"] = final_fields
            if result.get("title"):
                context.user_data["kn_title"] = result["title"]
            if kid:
                try:
                    models.set_knowledge_fields(kid, final_fields)
                except Exception:
                    logger.exception("ذخیرهٔ fields ناموفق")

            summary = result.get("summary") or "مصاحبه تمام شد."
            await _prompt_reply(
                update.message, context,
                f"✅ مصاحبه تمام شد.\n\n📋 *خلاصه*: {summary}\n\n"
                "▶️ ادامه برای ساخت فرم نهایی DANA...",
            )
            return await _final_assemble_and_preview(context, update.message)

        ask = result.get("ask")
        if not ask:
            await _prompt_reply(
                update.message, context,
                "⚠️ هوش مصنوعی نتوانست سؤال بعدی را بسازد. "
                "لطفاً پاسخ خود را کامل‌تر بنویسید، یا «✓ پایان مصاحبه» را بزنید.",
                reply_markup=_interview_continue_keyboard(),
            )
            return KN_INTERVIEW_LOOP
        # ثبت فیلدهای استخراج‌شده از این پاسخ
        extracted = result.get("extracted")
        if extracted:
            fields = context.user_data.setdefault("kn_fields", {})
            fields.update(extracted)
            if kid:
                try:
                    models.set_knowledge_fields(kid, fields)
                except Exception:
                    logger.exception("ذخیرهٔ fields ناموفق")

        # ثبت سؤال/پاسخ AI در تاریخچه
        history.append({"role": "assistant", "content": ask})
        if kid:
            try:
                models.set_knowledge_interview_history(kid, history)
            except Exception:
                logger.exception("ذخیرهٔ interview_history ناموفق")

        await _prompt_reply(
            update.message, context,
            f"🤖 *AI:* {ask}",
            reply_markup=_interview_continue_keyboard(),
        )
        return KN_INTERVIEW_LOOP

    except Exception as exc:
        logger.exception("خطا در kn_interview_loop_text")
        await update.message.reply_text(
            "🤖 هوش مصنوعی موقتاً پاسخ نمی‌دهد (احتمالاً قطعی سرویس).\n"
            "گزینه‌ها: دوباره همین پاسخ را بفرستید، یا «✓ پایان مصاحبه» را بزنید "
            "تا فیلدهای باقی‌مانده دستی از شما پرسیده شود.\n"
            f"(جزئیات فنی: {type(exc).__name__})"
        )
        return KN_INTERVIEW_LOOP


@_busy_ai
async def kn_interview_done(update, context) -> int:
    """callback_data: kn_interview:done — پایان زودهنگام توسط کاربر."""
    try:
        query = update.callback_query
        await query.answer()
        # فیلدهای پرشده تا الان نگه داشته میشود
        return await _final_assemble_and_preview(context, query.message)
    except Exception:
        logger.exception("خطا در kn_interview_done")
        return ConversationHandler.END


async def _final_assemble_and_preview(
    context,
    msg,
) -> int:
    """
    پایان مصاحبه → پاس polish فیلدبهفیلد + QA سبک → ورود به تنظیمات سازمانی.
    (پیش‌نویس نهایی پس از تکمیل تنظیمات سازمانی ساخته میشود تا درخت/پروژه و
    سایر فیلدهای فرم دانا حتماً در گزارش بنشینند.)
    """
    # قفل «در حال پردازش» — دستی (چون update نداریم)
    tid = context.user_data.get("_chat_id")
    if tid is None and msg.chat:
        tid = msg.chat_id
    if tid:
        set_busy(tid)

    try:
        from engine.knowledge_interview import polish_dana_draft, qa_flags

        await _prompt_reply(msg, context, "🔄 در حال ساخت فرم نهایی DANA...")
        knowledge_type = context.user_data["kn_type"]
        fields = context.user_data.get("kn_fields") or {}

        # پاس polish (بازنویسی فیلدبهفیلد + هشتگ + پروژه استخراجی)
        # شکست AI نباید کل ثبت را زمین بزند — بدون polish هم ادامه میدهیم.
        polish: dict = {}
        try:
            polish = await polish_dana_draft(
                knowledge_type,
                fields,
                raw_description=context.user_data.get("kn_description"),
            )
        except Exception:
            logger.warning("polish AI ناموفق — ادامه با همان متن کاربر")
            await _prompt_reply(
                msg, context,
                "⚠️ هوش مصنوعی موقتاً در دسترس نیست؛ فرم با همان متن شما ساخته می‌شود.",
            )

        context.user_data["kn_polished"] = polish or {}

        if polish.get("hashtags"):
            context.user_data["kn_hashtags"] = polish["hashtags"]
        if polish.get("extracted_project"):
            org = context.user_data.setdefault("kn_org_metadata", {})
            extracted = polish["extracted_project"].strip()
            current = (org.get("project") or "").strip()
            if not current:
                org["project"] = extracted
            elif extracted.lower() != current.lower():
                # پروژه ذکرشده در متن با پروفایل متفاوت است — اولویت با متن، با اطلاع به کاربر
                org["project"] = extracted
                org["_project_overridden"] = True
                org["_project_previous"] = current
                logger.info("پروژه از '%s' (پروفایل) به '%s' (متن) تغییر کرد", current, extracted)

        # همکاران/کمیته: اگر AI در مصاحبه آنها را به‌صورت فیلد استخراج کرده، به org_metadata منتقل کن تا در منو دیده شوند
        # (FIELD_SCHEMAS colleagues برای suggestion/explicit در fields می‌آید، ولی UI آن را از org_metadata می‌خواند)
        try:
            org2 = context.user_data.setdefault("kn_org_metadata", {})
            if fields.get("colleagues") and not (org2.get("colleagues") or "").strip():
                org2["colleagues"] = str(fields["colleagues"]).strip()
                logger.info("همکاران از fields به org_metadata منتقل شد: %s", org2["colleagues"])
            # اگر AI به‌صورت ضمنی کمیته را در fields یا extracted گذاشته بود
            for k in ("committee", "colleagues"):
                if fields.get(k) and not (org2.get(k) or "").strip():
                    org2[k] = str(fields[k]).strip()
        except Exception:
            logger.exception("همگام‌سازی colleagues/committee ناموفق")

        # QA سبک غیرمسدودکننده — فقط هشدار، بدون مسدودسازی
        flags: list[str] = []
        try:
            raw_flags = await qa_flags(
                knowledge_type, fields, context.user_data.get("kn_description"),
            )
            flags = [
                f"«{f.get('field', '')}»: {f.get('issue', '')}" for f in raw_flags
            ]
        except Exception:
            logger.exception("QA سبک ناموفق — بدون هشدار ادامه میدهیم")
        context.user_data["kn_qa_flags"] = flags

        text, kb = _org_menu_payload(context)
        # اگر پروژه از متن استخراج و جایگزین پروفایل شده، به کاربر اطلاع بده
        org_notify = context.user_data.get("kn_org_metadata") or {}
        project_notice = ""
        if org_notify.get("_project_overridden"):
            project_notice = (
                f"ℹ️ پروژه از «{org_notify.get('_project_previous') }» (پروفایل شما) "
                f"به «{org_notify.get('project') }» (ذکرشده در متن) تغییر کرد. "
                f"اگر درست نیست، از دکمه «🏗 تعیین پروژه» اصلاح کنید.\n\n"
            )
            org_notify.pop("_project_overridden", None)
            org_notify.pop("_project_previous", None)
        await _prompt_reply(
            msg, context,
            "✅ فرم محتوایی آماده شد.\n\n"
            + project_notice
            + "⚙️ *تنظیمات سازمانی* را تکمیل کنید (درخت دانش برای ثبت در دانا لازم است):\n\n"
            + text,
            reply_markup=kb,
        )
        return KN_ORG_META

    except Exception:
        logger.exception("خطا در _final_assemble_and_preview")
        await msg.reply_text("❌ خطا در ساخت فرم نهایی.")
        return ConversationHandler.END
    finally:
        if tid:
            clear_busy(tid)


async def kn_final_assemble_done(update, context) -> int:
    """callback_data: kn_final:done — placeholder اگر بعداً لازم شد."""
    await update.callback_query.answer()
    return KN_PREVIEW


# ══════════════════════════════════════════════════════════════════════════════
# تنظیمات سازمانی (درخت/کمیته/بذر/همکاران/هشتگ/محدوده)
# ══════════════════════════════════════════════════════════════════════════════

def _current_org_display(context, knowledge_type: str) -> dict:
    """برای نمایش در منو: مقدار فعلی هر مورد (نمایش کوتاه)."""
    org = context.user_data.get("kn_org_metadata") or {}
    tree_path = context.user_data.get("kn_tree_path") or []
    hashtags = context.user_data.get("kn_hashtags") or []

    def _short(value: str | None, limit: int = 24) -> str:
        if not value:
            return "خالی"
        v = str(value).strip()
        return v if len(v) <= limit else v[:limit - 1] + "…"

    return {
        "tree_display": " > ".join(tree_path) if tree_path else "انتخاب‌نشده",
        "committee_display": _short(org.get("committee")),
        "project_display": _short(org.get("project")),
        "colleagues_display": _short(org.get("colleagues")),
        "hashtags_display": _short(" ".join("#" + h for h in hashtags) if hashtags else None),
        "scope_display": _short(org.get("scope")),
    }


async def kn_org_tree(update, context) -> int:
    """ورود به تنظیم درخت دانش."""
    try:
        query = update.callback_query
        await query.answer()

        # دستیِ خالص بدون AI — پیشنهاد AI نده
        if context.user_data.get("kn_mode") in ("direct", "manual"):
            suggestions: list[dict] = []
            # برای نمایش وضعیت فعلی، knowledge_type را هم بگیر
            knowledge_type = context.user_data.get("kn_type", "")
        else:
            # پیشنهادهای AI فقط برای مصاحبه
            knowledge_type = context.user_data.get("kn_type")
            fields = context.user_data.get("kn_fields") or {}
            title = context.user_data.get("kn_title")
            raw_desc = context.user_data.get("kn_description") or ""
            suggestions: list[dict] = []
            try:
                from engine.knowledge_interview import suggest_tree_paths
                suggestions = await suggest_tree_paths(
                    knowledge_type, fields, raw_desc, title, top_k=3,
                )
            except Exception:
                logger.exception("خطا در گرفتن پیشنهاد درخت")

        context.user_data["kn_tree_suggestions"] = suggestions

        kb = _tree_mode_keyboard(suggestions)
        tree_display = _current_org_display(context, knowledge_type)["tree_display"]
        await query.edit_message_text(
            f"🌳 *انتخاب درخت دانش رسمی*\n\n"
            f"وضعیت فعلی: {tree_display}\n\n"
            "روش انتخاب را مشخص کنید:",
            parse_mode="Markdown",
            reply_markup=kb,
        )
        return KN_TREE
    except Exception:
        logger.exception("خطا در kn_org_tree")
        return KN_ORG_META


async def kn_org_committee(update, context) -> int:
    """شروع ویرایش کمیته تخصصی."""
    try:
        query = update.callback_query
        await query.answer()
        context.user_data["kn_org_pending_field"] = "committee"
        current = (context.user_data.get("kn_org_metadata") or {}).get("committee", "")
        await query.edit_message_text(
            f"👥 *کمیته تخصصی پیشنهادی*\n\n"
            f"مقدار فعلی: {current or 'خالی'}\n\n"
            "مقدار جدید را بنویسید (یا /skip برای رد کردن):",
            parse_mode="Markdown",
        )
        return KN_ORG_META
    except Exception:
        logger.exception("خطا در kn_org_committee")
        return KN_ORG_META


async def kn_org_project(update, context) -> int:
    """شروع ویرایش پروژه (درسآموخته ⭐ الزامی / دانش صریح اختیاری)."""
    try:
        query = update.callback_query
        await query.answer()
        context.user_data["kn_org_pending_field"] = "project"
        current = (context.user_data.get("kn_org_metadata") or {}).get("project", "")
        await query.edit_message_text(
            f"🏗 *پروژه*\n\n"
            f"مقدار فعلی: {current or 'خالی'}\n\n"
            "نام پروژه را بنویسید (یا /skip برای رد کردن):",
            parse_mode="Markdown",
        )
        return KN_ORG_META
    except Exception:
        logger.exception("خطا در kn_org_project")
        return KN_ORG_META


async def kn_org_colleagues(update, context) -> int:
    try:
        query = update.callback_query
        await query.answer()
        context.user_data["kn_org_pending_field"] = "colleagues"
        current = (context.user_data.get("kn_org_metadata") or {}).get("colleagues", "")
        await query.edit_message_text(
            f"🤝 *همکاران درگیر*\n\n"
            f"مقدار فعلی: {current or 'خالی'}\n\n"
            "نام همکاران (با کاما جدا کنید) را بنویسید:",
            parse_mode="Markdown",
        )
        return KN_ORG_META
    except Exception:
        logger.exception("خطا در kn_org_colleagues")
        return KN_ORG_META


async def kn_org_hashtags(update, context) -> int:
    try:
        query = update.callback_query
        await query.answer()
        context.user_data["kn_org_pending_field"] = "hashtags"
        current = " ".join("#" + h for h in (context.user_data.get("kn_hashtags") or []))
        await query.edit_message_text(
            f"#️⃣ *هشتگها*\n\n"
            f"مقدار فعلی: {current or 'خالی'}\n\n"
            "هشتگهای جدید را با فاصله بنویسید (مثلاً: `#ایمنی #جوشکاری`):",
            parse_mode="Markdown",
        )
        return KN_ORG_META
    except Exception:
        logger.exception("خطا در kn_org_hashtags")
        return KN_ORG_META


_SCOPE_OPTIONS = [
    "طراحی و مهندسی",
    "امور نیرو و بهره‌وری انرژی",
    "امور نیرو، تولید پراکنده و مقیاس متوسط",
    "امور نیرو و ساختمان‌های انرژی محور",
    "امور نیرو، توزیع و انتقال",
    "برنامه‌ریزی و انفورماتیک",
    "تامین",
    "بازاریابی و فروش",
    "حسابرسی داخلی",
    "سیستم‌ها و کیفیت",
    "مالی اقتصادی",
    "منابع انسانی",
]


async def kn_org_scope(update, context) -> int:
    try:
        query = update.callback_query
        await query.answer()
        current = (context.user_data.get("kn_org_metadata") or {}).get("scope", "")
        # نمایش ۱۲ گزینه به‌صورت دکمه (۲تایی در هر ردیف) + گزینه تایپ دستی
        rows: list[list[InlineKeyboardButton]] = []
        pair: list[InlineKeyboardButton] = []
        for opt in _SCOPE_OPTIONS:
            pair.append(InlineKeyboardButton(opt, callback_data=f"kn_org:scope_pick:{opt}"))
            if len(pair) == 2:
                rows.append(pair)
                pair = []
        if pair:
            rows.append(pair)
        rows.append([InlineKeyboardButton("✏️ تایپ دستی", callback_data="kn_org:scope_type")])
        rows.append([InlineKeyboardButton("↩️ بازگشت", callback_data="kn_org:back")])
        await query.edit_message_text(
            f"🏢 *محدوده سازمانی (حیطه)*\n\n"
            f"مقدار فعلی: {current or 'خالی'}\n\n"
            "یکی از گزینه‌ها را انتخاب کنید یا «تایپ دستی» را بزنید:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return KN_ORG_META
    except Exception:
        logger.exception("خطا در kn_org_scope")
        return KN_ORG_META


async def kn_org_scope_pick(update, context) -> int:
    """انتخاب حیطه از لیست ۱۲ گزینه‌ای."""
    try:
        query = update.callback_query
        await query.answer()
        value = query.data.split(":", 2)[2]
        org = context.user_data.setdefault("kn_org_metadata", {})
        org["scope"] = value
        kid = context.user_data.get(_KEY_ENTRY_ID)
        if kid:
            try:
                models.set_knowledge_org_metadata(kid, org)
            except Exception:
                logger.exception("ذخیرهٔ scope ناموفق")
        await query.edit_message_text(f"✓ حیطه ثبت شد: {value}")
        return await _show_org_menu(update, context)
    except Exception:
        logger.exception("خطا در kn_org_scope_pick")
        return KN_ORG_META


async def kn_org_scope_type(update, context) -> int:
    """حالت تایپ دستی برای حیطه."""
    try:
        query = update.callback_query
        await query.answer()
        context.user_data["kn_org_pending_field"] = "scope"
        await query.edit_message_text(
            "🏢 *محدوده سازمانی*\n\nمقدار را تایپ کنید:",
            parse_mode="Markdown",
        )
        return KN_ORG_META
    except Exception:
        logger.exception("خطا در kn_org_scope_type")
        return KN_ORG_META


async def kn_org_text_input(update, context) -> int:
    """دریافت متن برای فیلد در حال ویرایش."""
    try:
        field = context.user_data.get("kn_org_pending_field")
        if not field:
            # اگر pending نیست، منوی org_meta را دوباره نشان بده
            return await _show_org_menu(update, context)

        text = (update.message.text or "").strip()
        if not text:
            await _prompt_reply(update.message, context, "❌ متن خالی است. دوباره بنویسید یا /skip:")
            return KN_ORG_META

        if field == "hashtags":
            # پارس هشتگ: کلمات با #
            tags = []
            for token in text.replace("#", " ").split():
                t = token.strip()
                if t:
                    tags.append(t)
            tags = tags[:5]
            context.user_data["kn_hashtags"] = tags
        else:
            org = context.user_data.setdefault("kn_org_metadata", {})
            org[field] = text
            # ذخیره در DB
            kid = context.user_data.get(_KEY_ENTRY_ID)
            if kid:
                try:
                    models.set_knowledge_org_metadata(kid, org)
                except Exception:
                    logger.exception("ذخیرهٔ org_metadata ناموفق")

        context.user_data.pop("kn_org_pending_field", None)
        await delete_tracked(context)
        await update.message.reply_text(f"✓ {field} به‌روز شد.")
        return await _show_org_menu(update, context)

    except Exception:
        logger.exception("خطا در kn_org_text_input")
        return KN_ORG_META


def _org_menu_payload(context) -> tuple[str, InlineKeyboardMarkup]:
    """متن + کیبورد منوی تنظیمات سازمانی (برای نمایش از هر مسیری)."""
    knowledge_type = context.user_data.get("kn_type")
    display = _current_org_display(context, knowledge_type)
    kb = _org_meta_keyboard(knowledge_type, display)
    text_lines = ["⚙️ *تنظیمات سازمانی*", ""]
    text_lines.append(f"  🌳 درخت: {display['tree_display']}")
    if knowledge_type == "suggestion":
        text_lines.append(f"  👥 کمیته: {display['committee_display']}")
    star = " ⭐" if knowledge_type == "lesson" else ""
    text_lines.append(f"  🏗 پروژه{star}: {display['project_display']}")
    text_lines.append(f"  🤝 همکاران: {display['colleagues_display']}")
    text_lines.append(f"  #️⃣ هشتگها: {display['hashtags_display']}")
    if knowledge_type in ("lesson", "explicit"):
        text_lines.append(f"  🏢 محدوده: {display['scope_display']}")
    return "\n".join(text_lines), kb


async def _show_org_menu(update, context) -> int:
    """منوی org_meta را نشان میدهد (پس از ویرایش)."""
    text, kb = _org_menu_payload(context)

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=kb,
        )
        if update.callback_query.message:
            track_prompt(context, update.callback_query.message)
    else:
        await delete_tracked(context)
        sent = await update.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=kb,
        )
        track_prompt(context, sent)
    return KN_ORG_META


async def kn_org_done(update, context) -> int:
    """پایان تنظیمات سازمانی → ساخت پیش‌نویس نهایی + انتقال عکس‌ها + preview."""
    try:
        query = update.callback_query
        await query.answer()
        # همه‌چیز (ساخت گزارش با درخت/پروژه/Polish/QA، ذخیره، انتقال عکس‌ها)
        # در یک نقطه متمرکز است تا هیچ داده‌ای بین مراحل گم نشود.
        return await _save_and_preview(update.callback_query.message, context)
    except Exception:
        logger.exception("خطا در kn_org_done")
        return KN_ORG_META


async def kn_org_skip(update, context) -> int:
    """رد کردن کل تنظیمات سازمانی → preview بدون org_meta.
    [اصلاح باگ نسخه تلگرام: kn_org_done.callback وجود خارجی بود]"""
    try:
        query = update.callback_query
        await query.answer()
        # فراخوانی مستقیم تابع kn_org_done با همان update/context
        return await kn_org_done(update, context)
    except Exception:
        logger.exception("خطا در kn_org_skip")
        return KN_ORG_META


# ══════════════════════════════════════════════════════════════════════════════
# درخت دانش — drill-down + AI suggestion + type + skip
# ══════════════════════════════════════════════════════════════════════════════

async def kn_tree_ai(update, context) -> int:
    """نمایش پیشنهادهای AI."""
    try:
        query = update.callback_query
        await query.answer()
        suggestions = context.user_data.get("kn_tree_suggestions") or []
        if not suggestions:
            await query.edit_message_text(
                "⚠️ پیشنهادی از AI در دسترس نیست.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("↩️ بازگشت", callback_data="kn_tree:nav:back")],
                ]),
            )
            return KN_TREE
        kb = _tree_ai_suggestions_keyboard(suggestions)
        lines = ["💡 *پیشنهادهای AI برای درخت دانش*", ""]
        for i, s in enumerate(suggestions, start=1):
            path = " > ".join(s["path"])
            conf = int(s["confidence"] * 100)
            reason = s.get("reason", "")
            lines.append(f"  {i}. {path} ({conf}٪)")
            if reason:
                lines.append(f"     {reason}")
        lines.append("\nروی پیشنهاد مورد نظر کلیک کنید.")
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=kb)
        return KN_TREE
    except Exception:
        logger.exception("خطا در kn_tree_ai")
        return KN_TREE


async def kn_tree_ai_pick(update, context) -> int:
    """انتخاب یک پیشنهاد AI."""
    try:
        query = update.callback_query
        await query.answer()
        idx = int(query.data.split(":")[-1])
        suggestions = context.user_data.get("kn_tree_suggestions") or []
        if not (0 <= idx < len(suggestions)):
            await query.edit_message_text("⚠️ پیشنهاد نامعتبر.")
            return KN_TREE

        path = suggestions[idx]["path"]
        context.user_data["kn_tree_path"] = path
        kid = context.user_data.get(_KEY_ENTRY_ID)
        if kid:
            try:
                models.set_knowledge_tree_path(kid, path)
            except Exception:
                logger.exception("ذخیرهٔ tree_path ناموفق")

        # برگشت به منوی org_meta
        return await _show_org_menu(update, context)

    except Exception:
        logger.exception("خطا در kn_tree_ai_pick")
        return KN_TREE


# نگاشت پاسخ متنی به ایندکس پیشنهاد (صفحهٔ پیشنهادهای AI درخت)
_ORDINAL_PICKS: dict[str, int] = {
    "1": 0, "2": 1, "3": 2, "4": 3, "5": 4,
    "۱": 0, "۲": 1, "۳": 2, "۴": 3, "۵": 4,
    "اول": 0, "دوم": 1, "سوم": 2, "چهارم": 3, "پنجم": 4,
}


async def kn_tree_pick_text(update, context) -> int:
    """پاسخ متنی روی صفحهٔ پیشنهادهای AI — کاربر میتواند بگوید «اول» یا «۲»."""
    try:
        if context.user_data.get("kn_tree_typing"):
            return KN_TREE  # در حالت تایپ مسیر کامل مزاحم نشو
        suggestions = context.user_data.get("kn_tree_suggestions") or []
        if not suggestions:
            return KN_TREE

        text = (update.message.text or "").strip()
        idx = _ORDINAL_PICKS.get(text)
        if idx is None or not (0 <= idx < len(suggestions)):
            await _prompt_reply(
                update.message, context,
                "⚠️ لطفاً روی یکی از دکمه‌های پیشنهاد بزنید، یا شمارهٔ آن را بنویسید (مثلاً «اول»).",
            )
            return KN_TREE

        path = suggestions[idx]["path"]
        context.user_data["kn_tree_path"] = path
        kid = context.user_data.get(_KEY_ENTRY_ID)
        if kid:
            try:
                models.set_knowledge_tree_path(kid, path)
            except Exception:
                logger.exception("ذخیرهٔ tree_path ناموفق")

        await delete_tracked(context)
        await update.message.reply_text(f"✓ پیشنهاد شماره {idx + 1} ثبت شد.")
        return await _show_org_menu(update, context)
    except Exception:
        logger.exception("خطا در kn_tree_pick_text")
        return KN_TREE


async def kn_tree_nav_start(update, context) -> int:
    """callback_data: kn_tree:nav — نمایش سطح ریشه درخت."""
    try:
        query = update.callback_query
        await query.answer()
        from engine.knowledge_tree import KNOWLEDGE_TREE
        children = list(KNOWLEDGE_TREE.keys())
        # نمایش ریشه
        context.user_data["kn_tree_current_path"] = []
        kb = _tree_drill_keyboard(0, [], children)
        await query.edit_message_text(
            "🌳 *انتخاب دستی از درخت — سطح ریشه*\n\nیکی را انتخاب کنید:",
            parse_mode="Markdown",
            reply_markup=kb,
        )
        return KN_TREE
    except Exception:
        logger.exception("خطا در kn_tree_nav_start")
        return KN_TREE


async def kn_tree_nav(update, context) -> int:
    """callback_data: kn_tree:nav:<level>:<idx> — رفتن به فرزند idx در سطح level."""
    try:
        query = update.callback_query
        await query.answer()
        parts = query.data.split(":")
        level = int(parts[2])
        idx = int(parts[3])
        current_path = context.user_data.get("kn_tree_current_path") or []

        # برای سطح 1، children ریشه هستند
        if level == 1:
            children = []
            from engine.knowledge_tree import KNOWLEDGE_TREE
            for name in KNOWLEDGE_TREE.keys():
                children.append(name)
            if idx >= len(children):
                await query.edit_message_text("⚠️ انتخاب نامعتبر.")
                return KN_TREE
            current_path = [children[idx]]
        else:
            # children از سطح قبلی
            from engine.knowledge_tree import get_children
            children = get_children(current_path)
            if idx >= len(children):
                await query.edit_message_text("⚠️ انتخاب نامعتبر.")
                return KN_TREE
            current_path = current_path + [children[idx]]

        context.user_data["kn_tree_current_path"] = current_path

        from engine.knowledge_tree import get_children as _gc
        from engine.knowledge_tree import is_leaf as _il
        new_children = _gc(current_path)
        is_at_leaf = _il(current_path)

        if is_at_leaf or not new_children:
            # تأیید نهایی
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✓ تأیید این مسیر", callback_data="kn_tree:confirm")],
                [InlineKeyboardButton("↩️ بازگشت یک سطح", callback_data="kn_tree:nav:back")],
                [InlineKeyboardButton("🏠 منو", callback_data="menu:main")],
            ])
            await query.edit_message_text(
                f"🌳 مسیر انتخابی تاکنون:\n  {' > '.join(current_path)}\n\n"
                "این برگ است. تأیید کنید یا برگردید.",
                reply_markup=kb,
            )
            return KN_TREE

        kb = _tree_drill_keyboard(len(current_path), current_path, new_children)
        await query.edit_message_text(
            f"🌳 مسیر: {' > '.join(current_path)}\n\nفرزندان را انتخاب کنید:",
            reply_markup=kb,
        )
        return KN_TREE
    except Exception:
        logger.exception("خطا در kn_tree_nav")
        return KN_TREE


async def kn_tree_nav_back(update, context) -> int:
    """یک سطح به عقب.
    [اصلاح باگ نسخه تلگرام: kn_org_tree.callback وجود خارجی بود]"""
    try:
        query = update.callback_query
        await query.answer()
        current_path = context.user_data.get("kn_tree_current_path") or []
        if not current_path:
            # بازگشت به منوی انتخاب روش
            return await kn_org_tree(update, context)
        current_path = current_path[:-1]
        context.user_data["kn_tree_current_path"] = current_path

        from engine.knowledge_tree import get_children as _gc
        new_children = _gc(current_path)
        if not new_children:
            return await kn_org_tree(update, context)
        kb = _tree_drill_keyboard(len(current_path), current_path, new_children)
        await query.edit_message_text(
            f"🌳 مسیر: {' > '.join(current_path)}\n\nفرزندان را انتخاب کنید:",
            reply_markup=kb,
        )
        return KN_TREE
    except Exception:
        logger.exception("خطا در kn_tree_nav_back")
        return KN_TREE


async def kn_tree_confirm(update, context) -> int:
    """تأیید مسیر انتخابی."""
    try:
        query = update.callback_query
        await query.answer()
        path = context.user_data.get("kn_tree_current_path") or []
        if not path:
            await query.edit_message_text("⚠️ مسیر خالی است.")
            return KN_TREE

        context.user_data["kn_tree_path"] = path
        context.user_data.pop("kn_tree_current_path", None)
        kid = context.user_data.get(_KEY_ENTRY_ID)
        if kid:
            try:
                models.set_knowledge_tree_path(kid, path)
            except Exception:
                logger.exception("ذخیرهٔ tree_path ناموفق")

        return await _show_org_menu(update, context)
    except Exception:
        logger.exception("خطا در kn_tree_confirm")
        return KN_TREE


async def kn_tree_type(update, context) -> int:
    """شروع تایپ مسیر کامل."""
    try:
        query = update.callback_query
        await query.answer()
        context.user_data["kn_tree_typing"] = True
        await query.edit_message_text(
            "✏️ *تایپ مسیر کامل*\n\n"
            "مسیر را با > جدا کنید، از ریشه شروع کنید.\n"
            "مثال:\nMAPNA Development > HSE Management > Safety\n\n"
            "اگر اشتباه است، /skip کنید.",
            parse_mode="Markdown",
        )
        return KN_TREE
    except Exception:
        logger.exception("خطا در kn_tree_type")
        return KN_TREE


async def kn_tree_type_done(update, context) -> int:
    """پایان تایپ مسیر."""
    try:
        if not context.user_data.get("kn_tree_typing"):
            # پیام در حالت دیگری — نادیده بگیر
            return KN_ORG_META

        text = (update.message.text or "").strip()
        context.user_data.pop("kn_tree_typing", None)
        if not text or text.startswith("/skip"):
            await delete_tracked(context)
            await update.message.reply_text("↩️ رد شد.")
            return await _show_org_menu(update, context)

        path = [p.strip() for p in text.split(">") if p.strip()]
        from engine.knowledge_tree import validate_path
        if not validate_path(path):
            await _prompt_reply(
                update.message, context,
                "⚠️ مسیر در درخت رسمی یافت نشد. دوباره با /skip رد کنید یا از انتخاب دستی استفاده کنید.",
            )
            return KN_TREE

        context.user_data["kn_tree_path"] = path
        kid = context.user_data.get(_KEY_ENTRY_ID)
        if kid:
            try:
                models.set_knowledge_tree_path(kid, path)
            except Exception:
                logger.exception("ذخیرهٔ tree_path ناموفق")

        await delete_tracked(context)
        await update.message.reply_text(f"✓ مسیر ثبت شد: {' > '.join(path)}")
        return await _show_org_menu(update, context)
    except Exception:
        logger.exception("خطا در kn_tree_type_done")
        return KN_TREE


async def kn_tree_skip(update, context) -> int:
    """رد کردن انتخاب درخت."""
    try:
        query = update.callback_query
        await query.answer()
        return await _show_org_menu(update, context)
    except Exception:
        logger.exception("خطا در kn_tree_skip")
        return KN_TREE


# ══════════════════════════════════════════════════════════════════════════════
# ویرایش فیلد از preview
# ══════════════════════════════════════════════════════════════════════════════

def _editable_fields(knowledge_type: str) -> list[str]:
    """فیلدهای قابل ویرایش برای یک نوع دانش (به‌همراه impact_type/subtype دکمهای)."""
    from engine.knowledge_ai import BUTTON_FIELDS, FIELD_SCHEMAS
    keys = list(FIELD_SCHEMAS.get(knowledge_type, {}).keys())
    btn_keys = list(BUTTON_FIELDS.get(knowledge_type, {}).keys())
    return keys + btn_keys


async def kn_preview_to_edit(update, context) -> int:
    """callback_data: kn_edit:back — از preview وارد ویرایش فیلد."""
    try:
        query = update.callback_query
        await query.answer()
        knowledge_type = context.user_data.get("kn_type")
        keys = _editable_fields(knowledge_type)
        labels = {}
        from engine.knowledge_ai import FIELD_SCHEMAS
        labels.update(FIELD_SCHEMAS.get(knowledge_type, {}))
        from engine.knowledge_ai import BUTTON_FIELDS
        labels.update(BUTTON_FIELDS.get(knowledge_type, {}))
        kb = _field_edit_keyboard(keys, labels)
        await query.edit_message_text(
            "✏️ *ویرایش کدام فیلد؟*\n\nروی فیلد مورد نظر بزنید، یا برگردید.",
            parse_mode="Markdown",
            reply_markup=kb,
        )
        return KN_FIELD_EDIT
    except Exception:
        logger.exception("خطا در kn_preview_to_edit")
        return KN_PREVIEW


async def kn_edit_to_preview(update, context) -> int:
    """callback_data: kn_edit:back — از edit برگشت به preview."""
    try:
        query = update.callback_query
        await query.answer()
        # بازسازی گزارش
        report, draft = _build_draft(context)
        context.user_data["kn_report"] = report
        kid = context.user_data.get(_KEY_ENTRY_ID)
        if kid:
            try:
                models.set_knowledge_fields(kid, context.user_data.get("kn_fields") or {}, draft)
            except Exception:
                logger.exception("ذخیرهٔ draft ناموفق")
        await query.edit_message_text(
            f"📋 *پیش‌نمایش به‌روز شده:*\n\n{draft}",
            parse_mode="Markdown",
            reply_markup=_preview_keyboard(),
        )
        return KN_PREVIEW
    except Exception:
        logger.exception("خطا در kn_edit_to_preview")
        return KN_FIELD_EDIT


async def kn_edit_field(update, context) -> int:
    """callback_data: kn_edit:field:<key> — شروع ویرایش یک فیلد."""
    try:
        query = update.callback_query
        await query.answer()
        key = query.data.split(":", 2)[2]
        context.user_data["kn_edit_pending_key"] = key
        from engine.knowledge_ai import FIELD_SCHEMAS, BUTTON_FIELDS
        knowledge_type = context.user_data.get("kn_type")
        labels = {}
        labels.update(FIELD_SCHEMAS.get(knowledge_type, {}))
        labels.update(BUTTON_FIELDS.get(knowledge_type, {}))
        label = labels.get(key, key)

        # فیلدهای دکمهای (impact_type, subtype) → دکمه نه متن
        if key in BUTTON_FIELDS.get(knowledge_type, {}):
            options = BUTTON_FIELDS[knowledge_type][key]
            rows = [[InlineKeyboardButton(o, callback_data=f"kn_edit:btn:{o}")]
                    for o in options]
            rows.append([InlineKeyboardButton("↩️ انصراف", callback_data="kn_edit:back")])
            await query.edit_message_text(
                f"✏️ مقدار جدید برای «{label}» را انتخاب کنید:",
                reply_markup=InlineKeyboardMarkup(rows),
            )
            return KN_FIELD_EDIT

        # فیلد متنی
        current = (context.user_data.get("kn_fields") or {}).get(key, "")
        await query.edit_message_text(
            f"✏️ *ویرایش «{label}»*\n\n"
            f"مقدار فعلی: {current or 'خالی'}\n\n"
            "مقدار جدید را بنویسید (یا /skip برای رد کردن):",
            parse_mode="Markdown",
        )
        return KN_FIELD_EDIT
    except Exception:
        logger.exception("خطا در kn_edit_field")
        return KN_FIELD_EDIT


async def kn_edit_btn_choice(update, context) -> int:
    """callback_data: kn_edit:btn:<value> — ثبت مقدار دکمهای."""
    try:
        query = update.callback_query
        await query.answer()
        value = query.data.split(":", 2)[2]
        key = context.user_data.get("kn_edit_pending_key")
        if not key:
            return KN_FIELD_EDIT

        if key == "impact_type":
            context.user_data["kn_impact_type"] = value
        else:  # fields dict
            fields = context.user_data.setdefault("kn_fields", {})
            fields[key] = value
            kid = context.user_data.get(_KEY_ENTRY_ID)
            if kid:
                try:
                    models.set_knowledge_fields(kid, fields)
                except Exception:
                    logger.exception("ذخیرهٔ fields ناموفق")

        context.user_data.pop("kn_edit_pending_key", None)
        # بازگشت به منوی ویرایش
        knowledge_type = context.user_data.get("kn_type")
        keys = _editable_fields(knowledge_type)
        from engine.knowledge_ai import FIELD_SCHEMAS, BUTTON_FIELDS
        labels = {}
        labels.update(FIELD_SCHEMAS.get(knowledge_type, {}))
        labels.update(BUTTON_FIELDS.get(knowledge_type, {}))
        kb = _field_edit_keyboard(keys, labels)
        await query.edit_message_text(
            f"✓ {key} به {value} تغییر کرد.\n\nروی فیلد دیگری بزنید یا برگردید.",
            reply_markup=kb,
        )
        return KN_FIELD_EDIT
    except Exception:
        logger.exception("خطا در kn_edit_btn_choice")
        return KN_FIELD_EDIT


async def kn_edit_text_input(update, context) -> int:
    """متن جدید برای فیلد در حال ویرایش."""
    try:
        key = context.user_data.get("kn_edit_pending_key")
        if not key:
            return KN_FIELD_EDIT
        text = (update.message.text or "").strip()
        if not text or text.startswith("/skip"):
            context.user_data.pop("kn_edit_pending_key", None)
            await delete_tracked(context)
            await update.message.reply_text("↩️ رد شد.")
            return KN_FIELD_EDIT
        fields = context.user_data.setdefault("kn_fields", {})
        fields[key] = text
        context.user_data.pop("kn_edit_pending_key", None)
        kid = context.user_data.get(_KEY_ENTRY_ID)
        if kid:
            try:
                models.set_knowledge_fields(kid, fields)
            except Exception:
                logger.exception("ذخیرهٔ fields ناموفق")
        # بازگشت به منوی ویرایش
        knowledge_type = context.user_data.get("kn_type")
        keys = _editable_fields(knowledge_type)
        from engine.knowledge_ai import FIELD_SCHEMAS, BUTTON_FIELDS
        labels = {}
        labels.update(FIELD_SCHEMAS.get(knowledge_type, {}))
        labels.update(BUTTON_FIELDS.get(knowledge_type, {}))
        kb = _field_edit_keyboard(keys, labels)
        await delete_tracked(context)
        sent = await update.message.reply_text(
            f"✓ {key} به‌روز شد.\n\nروی فیلد دیگری بزنید یا برگردید.",
            reply_markup=kb,
        )
        track_prompt(context, sent)
        return KN_FIELD_EDIT
    except Exception:
        logger.exception("خطا در kn_edit_text_input")
        return KN_FIELD_EDIT


async def kn_photos_start(update, context) -> int:
    """callback_data: kn_photos_start — ورود به مرحلهٔ عکس از preview."""
    try:
        query = update.callback_query
        await query.answer()
        kb = _photos_done_keyboard()
        photos = context.user_data.setdefault("kn_photos", [])
        count_text = f" ({len(photos)} عکس ثبت شد)" if photos else ""
        await query.edit_message_text(
            f"📸 عکسهای مرتبط با تجربه را بفرستید (اختیاری، چندتایی هم میتوانید).{count_text}\n"
            "بعد از اتمام، دکمهٔ «پایان عکسها» را بزنید.",
            reply_markup=kb,
        )
        return KN_PHOTOS
    except Exception:
        logger.exception("خطا در kn_photos_start")
        return KN_PREVIEW


async def _cancel_knowledge_conv(update, context) -> int:
    knowledge_id = context.user_data.pop(_KEY_ENTRY_ID, None)
    if knowledge_id:
        try:
            set_knowledge_inactive(knowledge_id)
        except Exception:
            logger.exception("خطا در غیرفعال‌سازی پیش‌نویس دانش")
    for path in context.user_data.get("kn_photos", []):
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
    for key in list(context.user_data):
        if key.startswith("kn_") or key.startswith("_KEY_"):
            context.user_data.pop(key, None)
    await delete_tracked(context)
    await delete_tracked(context, "_attach_summary")
    telegram_id = update.effective_user.id
    if update.message:
        await update.message.reply_text("❌ عملیات لغو شد.", reply_markup=main_menu_keyboard(telegram_id))
    return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# ساخت ConversationHandler
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# بایگانی دانش — لیست، جستجو و جزئیات (فقط دانش‌های خود کاربر)
# ══════════════════════════════════════════════════════════════════════════════

_KN_ARCH_PAGE_SIZE = 6

_KN_TYPE_FA = {
    "lesson": "📖 درس‌آموخته",
    "suggestion": "💡 پیشنهاد بهبود",
    "explicit": "📚 دانش صریح",
}


def _kn_entry_title(entry: dict) -> str:
    """عنوان نمایشی — از هلپر مشترک engine (هدر پیش‌نویس DANA هرگز نمایش داده نمی‌شود)."""
    return entry_display_title(entry)


def _kn_status_fa(entry: dict) -> str:
    """برچسب وضعیت رکورد."""
    if entry.get("status") == "submitted":
        return "✅"
    return "✏️"


def _kn_arch_items(entries: list[dict]) -> list[dict]:
    """اقلام سبک لیست برای نگهداری در user_data (id/عنوان/وضعیت)."""
    return [
        {"id": e["id"], "title": _kn_entry_title(e), "submitted": e.get("status") == "submitted"}
        for e in entries
    ]


def _kn_arch_list_keyboard(items: list[dict], page: int) -> InlineKeyboardMarkup:
    total = len(items)
    start = page * _KN_ARCH_PAGE_SIZE
    chunk = items[start:start + _KN_ARCH_PAGE_SIZE]
    rows: list[list[InlineKeyboardButton]] = []
    for it in chunk:
        label = f"{_kn_status_fa(it)} {it['title']}"[:64]
        rows.append([InlineKeyboardButton(label, callback_data=f"kn:view:{it['id']}")])
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"kn:archpage:{page - 1}"))
    if start + _KN_ARCH_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"kn:archpage:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton("🔍 جستجو", callback_data="kn:search"),
        InlineKeyboardButton("📋 همه", callback_data="kn:list"),
    ])
    return InlineKeyboardMarkup(rows)


def _render_kn_arch_list(
    items: list[dict], page: int, header: str,
) -> tuple[str, InlineKeyboardMarkup]:
    pages = max(1, (len(items) + _KN_ARCH_PAGE_SIZE - 1) // _KN_ARCH_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    text = f"{header}\n\n📚 تعداد: {len(items)} مورد | صفحه {page + 1} از {pages}"
    if items:
        text += "\n\nبرای مشاهدهٔ جزئیات، یکی را انتخاب کنید:"
    return text, _kn_arch_list_keyboard(items, page)


@require_registration
async def kn_archive_list_entry(update, context) -> int:
    """ورود به بایگانی دانش — نمایش همهٔ دانش‌های خود کاربر. (callback: kn:list)"""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    entries = list_knowledge_by_user(user.id)
    items = _kn_arch_items(entries)
    context.user_data["kn_arch_items"] = items
    if not items:
        await query.edit_message_text(
            "📭 هنوز دانشی ثبت نکرده‌اید.\n\n"
            "با دکمهٔ «📝 ثبت دانش/تجربه سازمانی» اولین تجربه‌تان را مستند کنید!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📝 ثبت دانش", callback_data="kn:new")],
                [InlineKeyboardButton("🏠 بازگشت به منو", callback_data="menu:main")],
            ]),
        )
        return ConversationHandler.END
    context.user_data["kn_arch_page"] = 0
    text, kb = _render_kn_arch_list(items, 0, "📋 *بایگانی دانش‌های شما*")
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    return KN_ARCHIVE_LIST


@require_registration
async def kn_archive_search_entry(update, context) -> int:
    """ورود به جستجوی دانش‌ها — دریافت عبارت. (callback: kn:search)"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔍 *جستجو در دانش‌های شما*\n\n"
        "عبارت مورد نظر را بفرستید (بخشی از عنوان، متن، شمارهٔ دانش و...):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("↩️ بازگشت به لیست", callback_data="kn:list")],
        ]),
    )
    return KN_SEARCH_INPUT


@require_registration
async def kn_archive_search_input(update, context) -> int:
    """دریافت عبارت جستجو و نمایش نتایج."""
    keyword = _input_text(update, context)
    if not keyword:
        sent = await update.message.reply_text("❌ عبارت خالی است. دوباره بفرستید:")
        track_prompt(context, sent)
        return KN_SEARCH_INPUT
    user = update.effective_user
    entries = search_knowledge_by_user(user.id, keyword)
    items = _kn_arch_items(entries)
    context.user_data["kn_arch_items"] = items
    context.user_data["kn_arch_page"] = 0
    if not items:
        await update.message.reply_text(
            f"🔍 نتیجه‌ای برای «{keyword[:50]}» پیدا نشد.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 جستجوی دیگر", callback_data="kn:search")],
                [InlineKeyboardButton("📋 نمایش همه", callback_data="kn:list")],
                [InlineKeyboardButton("🏠 منو", callback_data="menu:main")],
            ]),
        )
        return KN_ARCHIVE_LIST
    text, kb = _render_kn_arch_list(items, 0, f"🔍 *نتایج جستجو* برای «{keyword[:40]}»")
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)
    return KN_ARCHIVE_LIST


@require_registration
async def kn_archive_page(update, context) -> int:
    """صفحه‌بندی لیست بایگانی. (callback: kn:archpage:N)"""
    query = update.callback_query
    await query.answer()
    page = int((query.data or "").split(":")[2])
    items = context.user_data.get("kn_arch_items") or []
    context.user_data["kn_arch_page"] = page
    text, kb = _render_kn_arch_list(items, page, "📋 *بایگانی دانش‌های شما*")
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    return KN_ARCHIVE_LIST


@require_registration
async def kn_archive_view(update, context) -> int:
    """
    مشاهدهٔ جزئیات یک دانش. (callback: kn:view:<id>)
    فقط مالکِ رکورد اجازهٔ دیدن دارد؛ همچنین لینک «🔗 مشاهده دانش»
    در مشاهدات ارتقایافته را فعال می‌کند.
    """
    query = update.callback_query
    await query.answer()
    kid = int((query.data or "").split(":")[2])
    user = update.effective_user
    entry = get_knowledge_entry_by_id(kid)
    profile = get_user_by_telegram_id(user.id)
    if not entry or not profile or entry.get("reported_by") != profile["id"]:
        await query.edit_message_text(
            "⚠️ رکورد یافت نشد یا به شما تعلق ندارد.",
            reply_markup=back_to_main_keyboard(),
        )
        return KN_ARCHIVE_LIST

    status = "✅ ثبت‌شده" if entry.get("status") == "submitted" else "✏️ پیش‌نویس"
    ktype = _KN_TYPE_FA.get(entry.get("knowledge_type"), "📚 دانش")
    created = (entry.get("created_at") or "")[:10]
    try:
        import datetime as _dt
        date_fa = jdatetime.date.fromgregorian(
            date=_dt.date.fromisoformat(created)
        ).strftime("%Y/%m/%d") if created else "—"
    except ValueError:
        date_fa = created or "—"

    desc = (entry.get("draft_text") or entry.get("raw_description") or "").strip()
    if len(desc) > 900:
        desc = desc[:900] + "…"

    org = entry.get("org_metadata_json") or {}
    tags = org.get("hashtags") if isinstance(org, dict) else None
    tag_line = " ".join("#" + str(t) for t in tags) if tags else "—"

    tree_path = entry.get("tree_path_json") or []
    tree_line = " ← ".join(str(p) for p in tree_path) if tree_path else "—"

    photos = models.list_knowledge_photos(kid)
    photo_line = f"{len(photos)} مورد" if photos else "—"
    pdf_note = "\n📎 فایل PDF/Word این گزارش تولید شده است." if entry.get("pdf_path") else ""

    kn_number = entry.get("kn_number") or "—"
    text = (
        f"📚 *جزئیات دانش*\n\n"
        f"🆔 شماره: {kn_number}\n"
        f"{ktype}\n"
        f"{status}\n"
        f"📅 تاریخ ثبت: {date_fa}\n"
        f"🌳 طبقه‌بندی: {tree_line}\n"
        f"#️⃣ هشتگ‌ها: {tag_line}\n"
        f"🖼️ پیوست‌ها: {photo_line}{pdf_note}\n\n"
        f"*متن:*\n{desc}"
    )
    rows = [[InlineKeyboardButton("◀️ بازگشت به لیست", callback_data="kn:list")]]
    rows.append([InlineKeyboardButton("🏠 منو", callback_data="menu:main")])
    await query.edit_message_text(
        text[:4000], parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )
    return KN_ARCHIVE_LIST


def get_knowledge_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(kn_mode_entry, pattern=r"^kn:new$"),
            CallbackQueryHandler(kn_promote_mode, pattern=r"^kn:promote:(manual|interview)$"),
            CallbackQueryHandler(kn_archive_list_entry, pattern=r"^kn:list$"),
            CallbackQueryHandler(kn_archive_search_entry, pattern=r"^kn:search$"),
            CallbackQueryHandler(kn_archive_view, pattern=r"^kn:view:\d+$"),
        ],
        states={
            KN_MODE_SELECT: [
                CallbackQueryHandler(kn_resume_yes, pattern=r"^kn_resume:yes$"),
                CallbackQueryHandler(kn_resume_no, pattern=r"^kn_resume:no$"),
                CallbackQueryHandler(kn_mode_manual, pattern=r"^kn_mode:manual$"),
                CallbackQueryHandler(kn_mode_direct, pattern=r"^kn_mode:direct$"),
                CallbackQueryHandler(kn_mode_interview, pattern=r"^kn_mode:interview$"),
            ],
            KN_TYPE: [
                CallbackQueryHandler(kn_type, pattern=r"^kn_type:(lesson|suggestion|explicit)$"),
            ],
            KN_TYPE_CONFIRM: [
                CallbackQueryHandler(kn_type_keep, pattern=r"^kn_type_keep$"),
                CallbackQueryHandler(kn_type_switch, pattern=r"^kn_type_switch:(lesson|suggestion|explicit)$"),
            ],
            KN_REPORTER_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, kn_reporter_name),
                MessageHandler(AUDIO_MESSAGE_FILTER, _voice_handler_for(kn_reporter_name)),
            ],
            KN_REPORTER_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, kn_reporter_title),
                MessageHandler(AUDIO_MESSAGE_FILTER, _voice_handler_for(kn_reporter_title)),
                CallbackQueryHandler(kn_reporter_title_skip, pattern=r"^kn_skip:title$"),
            ],
            KN_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, kn_description),
                MessageHandler(AUDIO_MESSAGE_FILTER, _voice_handler_for(kn_description)),
            ],
            KN_FIELD_ANSWER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, kn_field_answer),
                MessageHandler(AUDIO_MESSAGE_FILTER, _voice_handler_for(kn_field_answer)),
                CallbackQueryHandler(kn_field_skip, pattern=r"^kn_skip_field$"),
                CallbackQueryHandler(kn_impact, pattern=r"^kn_impact:(کیفی|کمی)$"),
            ],
            KN_FIELD_DIRECT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, kn_field_direct_input),
                MessageHandler(AUDIO_MESSAGE_FILTER, _voice_handler_for(kn_field_direct_input)),
                CallbackQueryHandler(kn_field_direct_skip, pattern=r"^kn_direct:skip$"),
                CallbackQueryHandler(kn_direct_btn_choice, pattern=r"^kn_direct:btn:.+$"),
            ],
            KN_INTERVIEW_FRAMEWORK: [
                CallbackQueryHandler(kn_interview_start, pattern=r"^kn_interview:start$"),
            ],
            KN_INTERVIEW_LOOP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, kn_interview_loop_text),
                MessageHandler(AUDIO_MESSAGE_FILTER, _voice_handler_for(kn_interview_loop_text)),
                CallbackQueryHandler(kn_interview_done, pattern=r"^kn_interview:done$"),
            ],
            KN_FINAL_ASSEMBLE: [
                CallbackQueryHandler(kn_final_assemble_done, pattern=r"^kn_final:done$"),
            ],
            KN_ORG_META: [
                CallbackQueryHandler(kn_org_tree, pattern=r"^kn_org:tree$"),
                CallbackQueryHandler(kn_org_committee, pattern=r"^kn_org:committee$"),
                CallbackQueryHandler(kn_org_project, pattern=r"^kn_org:project$"),
                CallbackQueryHandler(kn_org_colleagues, pattern=r"^kn_org:colleagues$"),
                CallbackQueryHandler(kn_org_hashtags, pattern=r"^kn_org:hashtags$"),
                CallbackQueryHandler(kn_org_scope, pattern=r"^kn_org:scope$"),
                CallbackQueryHandler(kn_org_scope_pick, pattern=r"^kn_org:scope_pick:.+$"),
                CallbackQueryHandler(kn_org_scope_type, pattern=r"^kn_org:scope_type$"),
                CallbackQueryHandler(_show_org_menu, pattern=r"^kn_org:back$"),
                CallbackQueryHandler(kn_org_done, pattern=r"^kn_org:done$"),
                CallbackQueryHandler(kn_org_skip, pattern=r"^kn_org:skip$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, kn_org_text_input),
                MessageHandler(AUDIO_MESSAGE_FILTER, _voice_handler_for(kn_org_text_input)),
            ],
            KN_TREE: [
                CallbackQueryHandler(kn_tree_ai, pattern=r"^kn_tree:ai$"),
                CallbackQueryHandler(kn_tree_ai_pick, pattern=r"^kn_tree:ai:pick:\d+$"),
                CallbackQueryHandler(kn_tree_nav_back, pattern=r"^kn_tree:nav:back$"),
                CallbackQueryHandler(kn_tree_nav_start, pattern=r"^kn_tree:nav$"),
                CallbackQueryHandler(kn_tree_nav, pattern=r"^kn_tree:nav:\d+:\d+$"),
                CallbackQueryHandler(kn_tree_confirm, pattern=r"^kn_tree:confirm$"),
                CallbackQueryHandler(kn_tree_skip, pattern=r"^kn_tree:skip$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, kn_tree_pick_text),
            ],
            KN_PREVIEW: [
                CallbackQueryHandler(kn_finish, pattern=r"^kn_finish$"),
                CallbackQueryHandler(kn_preview_to_edit, pattern=r"^kn_edit:back$"),
                CallbackQueryHandler(kn_photos_start, pattern=r"^kn_photos_start$"),
            ],
            KN_FIELD_EDIT: [
                CallbackQueryHandler(kn_edit_to_preview, pattern=r"^kn_edit:back$"),
                CallbackQueryHandler(kn_edit_field, pattern=r"^kn_edit:field:\w+$"),
                CallbackQueryHandler(kn_edit_btn_choice, pattern=r"^kn_edit:btn:.+$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, kn_edit_text_input),
                MessageHandler(AUDIO_MESSAGE_FILTER, _voice_handler_for(kn_edit_text_input)),
            ],
            KN_PHOTOS: [
                MessageHandler(filters.PHOTO, kn_photo_received),
                CallbackQueryHandler(kn_photos_done, pattern=r"^kn_photos_done$"),
            ],
            KN_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, kn_date),
                MessageHandler(AUDIO_MESSAGE_FILTER, _voice_handler_for(kn_date)),
                CallbackQueryHandler(kn_date_today, pattern=r"^kn_today$"),
                CallbackQueryHandler(kn_cal_open, pattern=r"^kndate:open$"),
                CallbackQueryHandler(kn_cal_nav, pattern=r"^kndate:view:\d+:\d+$"),
                CallbackQueryHandler(kn_cal_none, pattern=r"^kndate:none$"),
                CallbackQueryHandler(kn_cal_pick, pattern=r"^kndate:pick:\d+:\d+:\d+$"),
            ],
            KN_ARCHIVE_LIST: [
                CallbackQueryHandler(kn_archive_page, pattern=r"^kn:archpage:\d+$"),
                CallbackQueryHandler(kn_archive_view, pattern=r"^kn:view:\d+$"),
                CallbackQueryHandler(kn_archive_search_entry, pattern=r"^kn:search$"),
                CallbackQueryHandler(kn_archive_list_entry, pattern=r"^kn:list$"),
            ],
            KN_SEARCH_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, kn_archive_search_input),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", _cancel_knowledge_conv),
            CommandHandler("start", _cancel_knowledge_conv),
        ],
        per_message=False,
        name="knowledge_registration",
        persistent=False,
        allow_reentry=True,
    )
