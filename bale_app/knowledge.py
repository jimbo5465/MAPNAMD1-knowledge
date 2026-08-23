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
from db import models
from db.models import (
    get_user_by_telegram_id,
    add_knowledge_entry,
    get_knowledge_entry_by_id,
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
from bale_app.keyboards import main_menu_keyboard
from bale_app.framework import track_prompt, delete_tracked
from bale_app.jalali_calendar import (
    build_calendar_keyboard,
    parse_pick_data,
    parse_view_data,
)
from utils.busy_lock import clear_busy, is_busy, set_busy
from utils.dates import validate_jalali_date_str
from engine.knowledge_ai import extract_fields, FIELD_SCHEMAS, TYPE_LABELS
from engine.knowledge_draft import build_report, render_text
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
    KN_DESCRIPTION,        # شرح آزاد تجربه (روش دستی)
    KN_FIELD_ANSWER,       # پرسش فیلدهای ناقص (یکی یکی)
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
) = range(17)

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
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            "✍️ ثبت دستی — دانش آماده است",
            callback_data="kn_mode:manual",
        )],
    ]
    if ai_available:
        rows.append([
            InlineKeyboardButton(
                "🎙️ مصاحبه با AI — از صفر شروع میکنیم",
                callback_data="kn_mode:interview",
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
    """منوی تنظیمات سازمانی — هر ردیف یک مورد، یک دکمهٔ اقدام."""
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
        rows.append([
            InlineKeyboardButton(
                f"💡 بذر پیشنهاد: {current.get('seed_display', 'خالی')}",
                callback_data="kn_org:seed",
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
    if knowledge_type == "explicit":
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
        InlineKeyboardButton("✏️ تایپ مسیر کامل", callback_data="kn_tree:type"),
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
    """callback_data: kn_mode:manual — روش دستی: شرح آماده → AI → فیلدهای ناقص."""
    try:
        query = update.callback_query
        await query.answer()
        context.user_data["kn_mode"] = "manual"
        await query.edit_message_text(
            "✍️ *روش دستی*\n\n"
            "در این روش شما متن آمادهٔ تجربه/دانش خود را وارد میکنید؛ ربات آن را "
            "به فیلدهای DANA تبدیل میکند و فیلدهای ناقص را جداگانه میپرسد.\n\n"
            "🎙️ *راهنما:* میتوانید بهجای تایپ، ویس بفرستید — فارسی صحبت کنید و "
            "صدای شما توسط هوش مصنوعی به متن تبدیل میشود.\n\n"
            "📚 نوع دانش را انتخاب کنید:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(_type_buttons()),
        )
        return KN_TYPE

    except Exception:
        logger.exception("خطا در kn_mode_manual")
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

    # روش دستی: همان فلوی فعلی
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
    """مرحلهٔ عکس‌ها (اختیاری)."""
    photos = context.user_data.setdefault("kn_photos", [])
    count_text = f" ({len(photos)} عکس ثبت شد)" if photos else ""
    await _prompt_reply(
        msg, context,
        "📸 عکس‌های مرتبط با تجربه را بفرستید (اختیاری، چندتایی هم می‌توانید).\n"
        f"بعد از اتمام، دکمهٔ «پایان عکس‌ها» را بزنید.{count_text}",
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
        await update.callback_query.edit_message_text(
            f"🗓️ تاریخ ثبت را وارد کنید (فرمت {jdatetime.date.today().strftime('%Y/%m/%d')} سال/ماه/روز).\n"
            "برای امروز دکمهٔ زیر را بزنید:",
            reply_markup=_date_keyboard(),
        )
        return KN_DATE
    except Exception:
        logger.exception("خطا در kn_photos_done")
        return ConversationHandler.END


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
    return await _save_and_preview(update.message, context)


async def kn_date_today(update, context) -> int:
    """callback_data: kn_today"""
    await update.callback_query.answer()
    context.user_data["kn_date"] = jdatetime.date.today().strftime("%Y/%m/%d")
    msg = update.callback_query.message
    return await _save_and_preview(msg, context)


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
    msg = update.callback_query.message
    return await _save_and_preview(msg, context)


async def _save_and_preview(msg, context) -> int:
    """
    ذخیرهٔ پیش‌نویس (draft) در DB — در این لحظه id ساخته می‌شود؛
    عکس‌ها به پوشهٔ نهایی منتقل می‌شوند و پیش‌نمایش DANA نشان داده می‌شود.
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

        knowledge_type = context.user_data.get("kn_type")
        fields = context.user_data.get("kn_fields") or {}
        hashtags = context.user_data.get("kn_hashtags") or []
        impact_type = context.user_data.get("kn_impact_type")

        attachments = [f"{i:03d}.jpg" for i in range(1, len(context.user_data.get("kn_photos") or []) + 1)]

        report = build_report(
            knowledge_type=knowledge_type,
            title=_compute_title(context),
            fields=fields,
            hashtags=hashtags or None,
            impact_type=impact_type,
            project_name=None,
            contractor_name=None,
            reporter_name=context.user_data.get("kn_reporter_name") or "—",
            reporter_title=context.user_data.get("kn_reporter_title") or None,
            reported_date=context.user_data.get("kn_date") or "—",
            kn_number=None,
            raw_description=context.user_data.get("kn_description"),
            attachments=attachments,
        )
        draft = render_text(report)

        knowledge_id = add_knowledge_entry(
            telegram_id=chat_id,
            knowledge_type=knowledge_type,
            reporter_name=context.user_data.get("kn_reporter_name") or "—",
            reporter_title=context.user_data.get("kn_reporter_title") or None,
            raw_description=context.user_data.get("kn_description"),
            fields=fields,
            draft_text=draft,
            reported_date=context.user_data.get("kn_date") or None,
        )
        context.user_data[_KEY_ENTRY_ID] = knowledge_id
        context.user_data["kn_report"] = report

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
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تأیید و ثبت نهایی", callback_data="kn_finish")],
            [InlineKeyboardButton("❌ انصراف", callback_data="menu:main")],
        ])
        sent = await msg.reply_text(text, parse_mode="Markdown", reply_markup=kb)
        track_prompt(context, sent)
        return KN_PREVIEW

    except Exception:
        logger.exception("خطا در _save_and_preview دانش")
        await msg.reply_text("❌ خطایی رخ داد.")
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

        number = generate_knowledge_number(entry["project_id"])

        # بازسازی گزارش با شمارهٔ نهایی (گزارشِ ذخیره‌شده در مرحلهٔ پیش‌نمایش)
        report = context.user_data.get("kn_report")
        if not report:
            await query.edit_message_text("⚠️ گزارش یافت نشد.", reply_markup=main_menu_keyboard(update.effective_user.id))
            return ConversationHandler.END

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
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=pdf_path,
                    caption=f"📄 پیش‌نویس DANA (PDF) — {number}",
                    file_name=f"{number}.pdf",
                )
            except Exception:
                logger.exception("ارسال PDF دانش ناموفق")
        if docx_path and os.path.isfile(docx_path):
            try:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=docx_path,
                    caption=f"📝 پیش‌نویس DANA (Word) — {number}",
                    file_name=f"{number}.docx",
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
    # اولین نوبت: پیام کاربر خالی/آغازین تا AI سؤال اول را بدهد
    result = await interview_next_turn(
        context.user_data["kn_type"], history, "(شروع مصاحبه)"
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

        # فراخوانی AI
        result = await interview_next_turn(knowledge_type, history, user_text)

        # ثبت تاریخچه + فیلدها در DB برای resume
        kid = context.user_data.get(_KEY_ENTRY_ID)
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

    except Exception:
        logger.exception("خطا در kn_interview_loop_text")
        await update.message.reply_text("❌ خطایی رخ داد.")
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
    پاس نهایی polish با AI + ساخت گزارش + ذخیره draft + رفتن به KN_PREVIEW.
    """
    # قفل «در حال پردازش» — دستی (چون update نداریم)
    tid = context.user_data.get("_chat_id")
    if tid is None and msg.chat:
        tid = msg.chat_id
    if tid:
        set_busy(tid)

    try:
        from engine.knowledge_interview import polish_dana_draft

        await _prompt_reply(msg, context, "🔄 در حال ساخت فرم نهایی DANA...")
        knowledge_type = context.user_data["kn_type"]
        fields = context.user_data.get("kn_fields") or {}

        # پاس polish (narrative + hashtags + project/contractor از متن)
        polish = await polish_dana_draft(
            knowledge_type,
            fields,
            raw_description=context.user_data.get("kn_description"),
            project_name=None,
            contractor_name=None,
        )

        # ساخت گزارش با narrative AI در صورت موفقیت
        narrative = polish.get("narrative")
        hashtags = polish.get("hashtags")
        extracted_project = polish.get("extracted_project")
        extracted_contractor = polish.get("extracted_contractor")

        # ذخیره hashtag در context (اگر polish پیشنهاد داد)
        if hashtags:
            context.user_data["kn_hashtags"] = hashtags

        # ایجاد/به‌روزرسانی draft
        report = build_report(
            knowledge_type=knowledge_type,
            title=_compute_title(context),
            fields=fields,
            hashtags=hashtags,
            impact_type=context.user_data.get("kn_impact_type"),
            project_name=extracted_project,
            contractor_name=extracted_contractor,
            reporter_name=context.user_data.get("kn_reporter_name") or "—",
            reporter_title=context.user_data.get("kn_reporter_title") or None,
            reported_date=context.user_data.get("kn_date") or "—",
            kn_number=None,
            raw_description=context.user_data.get("kn_description"),
            attachments=[f"{i:03d}.jpg" for i in range(1, len(context.user_data.get("kn_photos") or []) + 1)],
            narrative_override=narrative,
        )
        draft = render_text(report)
        context.user_data["kn_report"] = report

        kid = context.user_data.get(_KEY_ENTRY_ID)
        if kid:
            try:
                models.set_knowledge_fields(kid, fields, draft)
            except Exception:
                logger.exception("ذخیرهٔ draft ناموفق")

        await _prompt_reply(
            msg, context,
            f"✅ فرم DANA آماده شد.\n\n📋 *پیش‌نمایش:*\n\n{draft}",
            reply_markup=_preview_keyboard(),
        )
        return KN_PREVIEW

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
        "seed_display": _short(org.get("seed")),
        "colleagues_display": _short(org.get("colleagues")),
        "hashtags_display": _short(" ".join("#" + h for h in hashtags) if hashtags else None),
        "scope_display": _short(org.get("scope")),
    }


async def kn_org_tree(update, context) -> int:
    """ورود به تنظیم درخت دانش."""
    try:
        query = update.callback_query
        await query.answer()

        # ابتدا سعی میکنیم پیشنهادهای AI را بگیریم
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


async def kn_org_seed(update, context) -> int:
    try:
        query = update.callback_query
        await query.answer()
        context.user_data["kn_org_pending_field"] = "seed"
        current = (context.user_data.get("kn_org_metadata") or {}).get("seed", "")
        await query.edit_message_text(
            f"💡 *بذر پیشنهاد*\n\n"
            f"مقدار فعلی: {current or 'خالی'}\n\n"
            "ایده اولیه از کجا آمد؟ بنویسید:",
            parse_mode="Markdown",
        )
        return KN_ORG_META
    except Exception:
        logger.exception("خطا در kn_org_seed")
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


async def kn_org_scope(update, context) -> int:
    try:
        query = update.callback_query
        await query.answer()
        context.user_data["kn_org_pending_field"] = "scope"
        current = (context.user_data.get("kn_org_metadata") or {}).get("scope", "")
        await query.edit_message_text(
            f"🏢 *محدوده سازمانی*\n\n"
            f"مقدار فعلی: {current or 'خالی'}\n\n"
            "این دانش به کدام بخشهای سازمان مربوط است؟",
            parse_mode="Markdown",
        )
        return KN_ORG_META
    except Exception:
        logger.exception("خطا در kn_org_scope")
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


async def _show_org_menu(update, context) -> int:
    """منوی org_meta را نشان میدهد (پس از ویرایش)."""
    knowledge_type = context.user_data.get("kn_type")
    display = _current_org_display(context, knowledge_type)
    kb = _org_meta_keyboard(knowledge_type, display)
    text_lines = ["⚙️ *تنظیمات سازمانی*", ""]
    text_lines.append(f"  🌳 درخت: {display['tree_display']}")
    if knowledge_type == "suggestion":
        text_lines.append(f"  👥 کمیته: {display['committee_display']}")
        text_lines.append(f"  💡 بذر: {display['seed_display']}")
    text_lines.append(f"  🤝 همکاران: {display['colleagues_display']}")
    text_lines.append(f"  #️⃣ هشتگها: {display['hashtags_display']}")
    if knowledge_type == "explicit":
        text_lines.append(f"  🏢 محدوده: {display['scope_display']}")

    if update.callback_query:
        await update.callback_query.edit_message_text(
            "\n".join(text_lines),
            parse_mode="Markdown",
            reply_markup=kb,
        )
        if update.callback_query.message:
            track_prompt(context, update.callback_query.message)
    else:
        await delete_tracked(context)
        sent = await update.message.reply_text(
            "\n".join(text_lines),
            parse_mode="Markdown",
            reply_markup=kb,
        )
        track_prompt(context, sent)
    return KN_ORG_META


async def kn_org_done(update, context) -> int:
    """پایان تنظیمات سازمانی → بازسازی گزارش + preview."""
    try:
        query = update.callback_query
        await query.answer()

        # بازسازی گزارش با تنظیمات جدید
        knowledge_type = context.user_data["kn_type"]
        org = context.user_data.get("kn_org_metadata") or {}
        tree_path = context.user_data.get("kn_tree_path")

        report = build_report(
            knowledge_type=knowledge_type,
            title=_compute_title(context),
            fields=context.user_data.get("kn_fields") or {},
            hashtags=context.user_data.get("kn_hashtags") or None,
            impact_type=context.user_data.get("kn_impact_type"),
            project_name=None,
            contractor_name=None,
            reporter_name=context.user_data.get("kn_reporter_name") or "—",
            reporter_title=context.user_data.get("kn_reporter_title") or None,
            reported_date=context.user_data.get("kn_date") or "—",
            kn_number=None,
            raw_description=context.user_data.get("kn_description"),
            attachments=[f"{i:03d}.jpg" for i in range(1, len(context.user_data.get("kn_photos") or []) + 1)],
            narrative_override=None,
            tree_path=tree_path,
            org_metadata=org,
        )
        draft = render_text(report)
        context.user_data["kn_report"] = report
        kid = context.user_data.get(_KEY_ENTRY_ID)
        if kid:
            try:
                models.set_knowledge_fields(kid, context.user_data.get("kn_fields") or {}, draft)
            except Exception:
                logger.exception("ذخیرهٔ draft ناموفق")

        await query.edit_message_text(
            f"✅ تنظیمات سازمانی ذخیره شد.\n\n📋 *پیش‌نمایش:*\n\n{draft}",
            parse_mode="Markdown",
            reply_markup=_preview_keyboard(),
        )
        return KN_PREVIEW
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
        knowledge_type = context.user_data.get("kn_type")
        report = build_report(
            knowledge_type=knowledge_type,
            title=_compute_title(context),
            fields=context.user_data.get("kn_fields") or {},
            hashtags=context.user_data.get("kn_hashtags") or None,
            impact_type=context.user_data.get("kn_impact_type"),
            project_name=None,
            contractor_name=None,
            reporter_name=context.user_data.get("kn_reporter_name") or "—",
            reporter_title=context.user_data.get("kn_reporter_title") or None,
            reported_date=context.user_data.get("kn_date") or "—",
            kn_number=None,
            raw_description=context.user_data.get("kn_description"),
            attachments=[f"{i:03d}.jpg" for i in range(1, len(context.user_data.get("kn_photos") or []) + 1)],
            narrative_override=None,
            tree_path=context.user_data.get("kn_tree_path"),
            org_metadata=context.user_data.get("kn_org_metadata"),
        )
        draft = render_text(report)
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

def get_knowledge_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(kn_mode_entry, pattern=r"^kn:new$"),
            CallbackQueryHandler(kn_promote_mode, pattern=r"^kn:promote:(manual|interview)$"),
        ],
        states={
            KN_MODE_SELECT: [
                CallbackQueryHandler(kn_resume_yes, pattern=r"^kn_resume:yes$"),
                CallbackQueryHandler(kn_resume_no, pattern=r"^kn_resume:no$"),
                CallbackQueryHandler(kn_mode_manual, pattern=r"^kn_mode:manual$"),
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
                CallbackQueryHandler(kn_org_seed, pattern=r"^kn_org:seed$"),
                CallbackQueryHandler(kn_org_colleagues, pattern=r"^kn_org:colleagues$"),
                CallbackQueryHandler(kn_org_hashtags, pattern=r"^kn_org:hashtags$"),
                CallbackQueryHandler(kn_org_scope, pattern=r"^kn_org:scope$"),
                CallbackQueryHandler(kn_org_done, pattern=r"^kn_org:done$"),
                CallbackQueryHandler(kn_org_skip, pattern=r"^kn_org:skip$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, kn_org_text_input),
                MessageHandler(AUDIO_MESSAGE_FILTER, _voice_handler_for(kn_org_text_input)),
            ],
            KN_TREE: [
                CallbackQueryHandler(kn_tree_ai, pattern=r"^kn_tree:ai$"),
                CallbackQueryHandler(kn_tree_ai_pick, pattern=r"^kn_tree:ai:pick:\d+$"),
                CallbackQueryHandler(kn_tree_nav_back, pattern=r"^kn_tree:nav:back$"),
                CallbackQueryHandler(kn_tree_nav, pattern=r"^kn_tree:nav:\d+:\d+$"),
                CallbackQueryHandler(kn_tree_confirm, pattern=r"^kn_tree:confirm$"),
                CallbackQueryHandler(kn_tree_type, pattern=r"^kn_tree:type$"),
                CallbackQueryHandler(kn_tree_skip, pattern=r"^kn_tree:skip$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, kn_tree_type_done),
                MessageHandler(AUDIO_MESSAGE_FILTER, _voice_handler_for(kn_tree_type_done)),
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
