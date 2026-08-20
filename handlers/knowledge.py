"""
ماژول knowledge — ثبت دانش/تجربه سازمانی از طریق تلگرام (فاز دانش سازمانی).

فلوی مکالمه:
    پروژه ← پیمانکار ← گزارش‌دهنده (نام/سمت) ← نوع دانش
    ← شرح آزاد تجربه ← [استخراج فیلدها با AI ← پیشنهاد طبقه‌بندی (اختیاری)
        ← پرسش فیلدهای ناقص یک‌به‌یک (شامل «تاثیر اجرای پیشنهاد» برای پیشنهاد)]
    ← عکس‌ها (اختیاری) ← تاریخ ← پیش‌نمایش (پیش‌نویس DANA) ← ثبت نهایی
        (شماره‌دهی خودکار KN + ارسال پیش‌نویس DANA + فایل‌های PDF و DOCX)

قوانین قفل‌شده:
  - هر سطح (۱/۲/۳) فقط در scope خودش پروژه/پیمانکار انتخاب می‌کند
    (همان helpers های handlers/auth.py).
  - نوع دانش توسط اپراتور انتخاب می‌شود: lesson | suggestion | explicit.
  - فیلدها ابتدا با AI از شرح آزاد استخراج می‌شوند؛ فیلدهای استخراج‌نشده
    یک‌به‌یک از اپراتور پرسیده می‌شوند. اگر AI غیرفعال باشد، همهٔ فیلدها
    دستی پرسیده می‌شوند (fallback امن).
  - اگر AI نوعِ دیگری را تشخیص دهد، از اپراتور تأیید می‌گیرد (نگه‌داشتن یا تغییر).
  - رکورد در حالت draft در مرحلهٔ پیش‌نمایش ذخیره می‌شود؛ انصراف =
    set_knowledge_inactive. ثبت نهایی فقط از طریق دکمهٔ تأیید.
  - شمارهٔ KN فقط در لحظهٔ ثبت نهایی تولید می‌شود (engine/knowledge_numbering).
  - خروجی ثبت = پیش‌نویس DANA (engine/knowledge_draft) + فایل PDF و DOCX
    (engine/knowledge_render) که همان لحظه برای کاربر ارسال می‌شود.

callback_data قفل‌شده: kn:new | kn_proj:<id> | kn_ctr:<id>
   | kn_type:<lesson|suggestion|explicit> | kn_skip:title
   | kn_type_keep | kn_type_switch:<type> | kn_impact:<کیفی|کمی>
   | kn_skip_field | kn_photos_done | kn_today | kn_finish
"""

from __future__ import annotations

import logging
import os

import jdatetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

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
    list_knowledge_photos,
)
from handlers.auth import require_registration
from handlers.keyboards import back_to_main_keyboard, main_menu_keyboard
from utils.dates import validate_jalali_date_str
from engine.knowledge_ai import extract_fields, FIELD_SCHEMAS, TYPE_LABELS
from engine.knowledge_draft import build_report, render_text
from engine.knowledge_render import render_dana_pdf, render_dana_docx

logger = logging.getLogger(__name__)

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


def _input_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """
    متن ورودی کاربر را برمی‌گرداند.
    اگر پیام ویس بوده، متن ترنسکرایب‌شده در kn_pending_text ذخیره شده است.
    """
    pending = context.user_data.pop("kn_pending_text", None)
    if pending:
        return pending
    return (update.message.text or "") if update.message else ""


# ── تبدیل گفتار به متن (STT) — Groq Whisper ──────────────────────────────────

_STT_TMP_DIR = "/tmp/welderbot_stt"


async def _transcribe_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """ویس/فایل صوتی تلگرام را دانلود و با Groq Whisper به متن تبدیل می‌کند.

    خروجی: متن استخراج‌شده یا None در صورت خطا.
    """
    if not config.GROQ_API_KEY:
        await update.message.reply_text(
            "⚠️ تبدیل صدا به متن پیکربندی نشده است. لطفاً به‌جای ویس، متن بنویسید."
        )
        return None

    import httpx

    # ۱. پیدا کردن فایل صوتی (voice / audio / video note)
    msg = update.message
    file_id = None
    if msg.voice:
        file_id = msg.voice.file_id
    elif msg.audio:
        file_id = msg.audio.file_id
    elif msg.video_note:
        file_id = msg.video_note.file_id
    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith("audio"):
        file_id = msg.document.file_id

    if not file_id:
        await update.message.reply_text("❌ فایل صوتی شناخته‌شده‌ای پیدا نکردم. لطفاً ویس بفرستید یا متن بنویسید.")
        return None

    # ۲. دانلود فایل
    try:
        file = await context.bot.get_file(file_id)
        os.makedirs(_STT_TMP_DIR, exist_ok=True)
        tmp_path = os.path.join(_STT_TMP_DIR, f"voice_{update.effective_user.id}_{file_id[:20]}.ogg")
        await file.download_to_drive(tmp_path)
    except Exception:
        logger.exception("خطا در دانلود فایل صوتی")
        await update.message.reply_text("❌ دانلود فایل صوتی ناموفق بود. دوباره تلاش کنید یا متن بنویسید.")
        return None

    # ۳. ارسال به Groq Whisper
    try:
        url = f"{config.GROQ_STT_BASE_URL.rstrip('/')}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {config.GROQ_API_KEY}"}
        with open(tmp_path, "rb") as f:
            files = {"file": (os.path.basename(tmp_path), f, "audio/ogg")}
            data = {"model": config.GROQ_STT_MODEL, "language": "fa", "response_format": "json"}
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, headers=headers, files=files, data=data)
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


async def kn_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """هندلر سراسری ویس/فایل صوتی در مکالمهٔ ثبت دانش.

    متن ترنسکرایب‌شده را در kn_pending_text می‌گذارد و handler متنی مناسب را صدا می‌زند.
    """
    text = await _transcribe_voice(update, context)
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
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        text = await _transcribe_voice(update, context)
        if text is None:
            return ConversationHandler.END
        context.user_data["kn_pending_text"] = text
        return await target_handler(update, context)
    return wrapper


class _AudioMessageFilter(filters.MessageFilter):
    """فیلتر پیام‌های صوتی: voice / audio / video_note / document صوتی."""

    def filter(self, message):
        if not message:
            return False
        if message.voice or message.audio or message.video_note:
            return True
        if message.document and message.document.mime_type:
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
    # دکمه‌ها ۲ ستونه برای جمع‌و‌جور بودن
    pairs: list[InlineKeyboardButton] = []
    for i, name in enumerate(options):
        cb = f"kn_tree:nav:{level + 1}:{i}"
        pairs.append(InlineKeyboardButton(name, callback_data=cb))
        if len(pairs) == 2:
            rows.append(pairs)
            pairs = []
    if pairs:
        rows.append(pairs)
    # اگر در سطح برگ هستیم (level+1 == 4 یا options خالیست)، دکمهٔ تأیید
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
        [InlineKeyboardButton(f"📅 امروز ({today})", callback_data="kn_today")]
    ])


def _compute_title(context: ContextTypes.DEFAULT_TYPE) -> str:
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
# نقطه ورود — انتخاب پروژه
# ══════════════════════════════════════════════════════════════════════════════

@require_registration
async def kn_mode_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    callback_data: kn:new
    ابتدا بررسی میکند آیا کاربر ثبت ناتمامی دارد (resume)؛ سپس زیرمنوی انتخاب روش.
    """
    try:
        query = update.callback_query
        await query.answer()

        telegram_id = update.effective_user.id
        pending = models.find_pending_knowledge_by_user(telegram_id)
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
        return KN_MODE_SELECT

    except Exception:
        logger.exception("خطا در kn_mode_entry")
        try:
            await update.callback_query.answer("❌ خطایی رخ داد.", show_alert=True)
        except Exception:
            pass
        return ConversationHandler.END


async def kn_resume_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """callback_data: kn_resume:yes — ادامه ثبت ناتمام."""
    try:
        query = update.callback_query
        await query.answer()
        pending_id = context.user_data.pop("kn_resume_pending_id", None)
        if not pending_id:
            await query.edit_message_text(
                "⚠️ ثبت ناتمام یافت نشد.",
                reply_markup=back_to_main_keyboard(),
            )
            return ConversationHandler.END

        entry = models.get_knowledge_entry_by_id(pending_id)
        if not entry:
            await query.edit_message_text(
                "⚠️ رکورد یافت نشد.",
                reply_markup=back_to_main_keyboard(),
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


async def kn_resume_no(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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


async def kn_mode_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """callback_data: kn_mode:manual — روش دستت: شرح آماده → AI → فیلدهای ناقص."""
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


async def kn_mode_interview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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


async def kn_promote_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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


async def kn_reporter_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        name = _clean_text(_input_text(update, context), _MAX_NAME)
        if name is None:
            await update.message.reply_text("❌ نام نامعتبر است. دوباره وارد کنید:")
            return KN_REPORTER_NAME
        context.user_data["kn_reporter_name"] = name
        await update.message.reply_text(
            "💼 سمت گزارش‌دهنده را وارد کنید (مثلاً «سرپرست شیفت»)، یا رد کنید:",
            reply_markup=_skip_title_keyboard(),
        )
        return KN_REPORTER_TITLE
    except Exception:
        logger.exception("خطا در kn_reporter_name")
        await update.message.reply_text("❌ خطایی رخ داد.")
        return ConversationHandler.END


async def kn_reporter_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = _clean_text(_input_text(update, context), _MAX_NAME)
    if value is None:
        await update.message.reply_text("❌ سمت نامعتبر است. دوباره وارد کنید:")
        return KN_REPORTER_TITLE
    context.user_data["kn_reporter_title"] = value
    await update.message.reply_text(
        "📚 نوع دانش را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(_type_buttons()),
    )
    return KN_TYPE


async def kn_reporter_title_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    context.user_data["kn_reporter_title"] = ""
    await update.callback_query.edit_message_text(
        "📚 نوع دانش را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(_type_buttons()),
    )
    return KN_TYPE


async def kn_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """callback_data: kn_type:<lesson|suggestion|explicit>"""
    await update.callback_query.answer()
    value = update.callback_query.data.split(":", 1)[1]
    if value not in FIELD_SCHEMAS:
        await update.callback_query.edit_message_text(
            "⚠️ نوع دانش نامعتبر است.", reply_markup=back_to_main_keyboard()
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


async def kn_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شرح آزاد ← استخراج با AI ← [تأیید طبقه‌بندی] ← پرسش فیلدهای ناقص."""
    desc = _clean_text(_input_text(update, context), _MAX_DESC)
    if desc is None:
        await update.message.reply_text(
            f"❌ شرح نامعتبر است (حداکثر {_MAX_DESC} کاراکتر). دوباره بنویسید:"
        )
        return KN_DESCRIPTION
    context.user_data["kn_description"] = desc

    await update.message.reply_text("🔄 در حال استخراج فیلدها با هوش مصنوعی...")

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
        await update.message.reply_text("\n".join(summary), parse_mode="Markdown")

    # پیشنهاد طبقه‌بندی AI (مطابق knowledge-classification.md) —
    # اگر با انتخاب اپراتور تعارض دارد، تأیید بگیر
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
        await update.message.reply_text(msg, parse_mode="Markdown",
                                        reply_markup=_type_conflict_keyboard(recommended))
        return KN_TYPE_CONFIRM

    return await _continue_after_extraction(update.message, context)


async def _continue_after_extraction(msg, context: ContextTypes.DEFAULT_TYPE) -> int:
    """بعد از استخراج (و تأیید نوع): پرسش فیلدهای ناقص یا رفتن به عکس‌ها."""
    missing = context.user_data.get("kn_missing") or []
    if missing:
        return await _ask_next_field(msg, context)
    return await _open_photos(msg, context)


async def kn_type_keep(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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


async def kn_type_switch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
        await query.message.reply_text("\n".join(summary), parse_mode="Markdown")

        return await _continue_after_extraction(query.message, context)
    except Exception:
        logger.exception("خطا در kn_type_switch")
        return ConversationHandler.END


async def kn_impact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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


async def _ask_next_field(msg, context: ContextTypes.DEFAULT_TYPE) -> int:
    """فیلد ناقص بعدی را می‌پرسد؛ اگر همه پر شدند به عکس‌ها می‌رود."""
    missing = context.user_data.get("kn_missing") or []
    if not missing:
        return await _open_photos(msg, context)

    key = missing.pop(0)
    context.user_data["kn_missing"] = missing
    context.user_data["kn_current_field"] = key

    # «تاثیر اجرای پیشنهاد» یک انتخاب دوگانه است نه متن آزاد
    if key == "impact_type":
        await msg.reply_text(
            "📊 *تاثیر اجرای پیشنهاد* چیست؟\n"
            "«کیفی» = اثر بدون رقم مشخص | «کمی» = اثر با عدد/درصد/مبلغ",
            parse_mode="Markdown",
            reply_markup=_impact_keyboard(),
        )
        return KN_FIELD_ANSWER

    knowledge_type = context.user_data.get("kn_type")
    label = FIELD_SCHEMAS.get(knowledge_type, {}).get(key, key)
    await msg.reply_text(
        f"✍️ *{label}* را وارد کنید:",
        parse_mode="Markdown",
        reply_markup=_field_skip_keyboard(),
    )
    return KN_FIELD_ANSWER


async def kn_field_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """پاسخ به فیلد ناقص جاری (متن)."""
    try:
        value = _clean_text(_input_text(update, context), _MAX_FIELD)
        if value is None:
            await update.message.reply_text(
                f"❌ پاسخ نامعتبر است (حداکثر {_MAX_FIELD} کاراکتر). دوباره بنویسید:"
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


async def kn_field_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """callback_data: kn_skip_field — رد کردن فیلد ناقص جاری."""
    try:
        query = update.callback_query
        await query.answer()
        return await _ask_next_field(query.message, context)
    except Exception:
        logger.exception("خطا در kn_field_skip")
        return ConversationHandler.END


async def _open_photos(msg, context: ContextTypes.DEFAULT_TYPE) -> int:
    """مرحلهٔ عکس‌ها (اختیاری)."""
    photos = context.user_data.setdefault("kn_photos", [])
    count_text = f" ({len(photos)} عکس ثبت شد)" if photos else ""
    await msg.reply_text(
        "📸 عکس‌های مرتبط با تجربه را بفرستید (اختیاری، چندتایی هم می‌توانید).\n"
        f"بعد از اتمام، دکمهٔ «پایان عکس‌ها» را بزنید.{count_text}",
        reply_markup=_photos_done_keyboard(),
    )
    return KN_PHOTOS


async def kn_photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """عکس‌ها در پوشهٔ pending نگه داشته و هنگام ثبت نهایی منتقل می‌شوند."""
    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)

        user_dir = os.path.join(config.KN_PHOTO_PATH, "pending", str(update.effective_user.id))
        os.makedirs(user_dir, exist_ok=True)
        num = len(context.user_data.get("kn_photos", [])) + 1
        full_path = os.path.join(user_dir, f"{num:03d}.jpg")
        await file.download_to_drive(full_path)

        photos = context.user_data.setdefault("kn_photos", [])
        photos.append(full_path)

        await update.message.reply_text(
            f"✅ عکس {len(photos)} ذخیره شد. اگر عکس دیگری هست بفرستید، وگرنه «پایان عکس‌ها» را بزنید.",
            reply_markup=_photos_done_keyboard(),
        )
        return KN_PHOTOS
    except Exception:
        logger.exception("خطا در ذخیره عکس دانش")
        await update.message.reply_text("❌ ذخیره عکس ناموفق بود. دوباره تلاش کنید:")
        return KN_PHOTOS


async def kn_photos_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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


async def kn_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = _clean_text(update.message.text, 30)
    if not value:
        await update.message.reply_text("❌ تاریخ خالی است. دوباره وارد کنید:")
        return KN_DATE
    ok, _err = validate_jalali_date_str(value)
    if not ok:
        await update.message.reply_text("❌ تاریخ نامعتبر است. مثال: ۱۴۰۴/۰۵/۱۲")
        return KN_DATE
    normalized = value.replace("-", "/")
    context.user_data["kn_date"] = normalized
    return await _save_and_preview(update.message, context)


async def kn_date_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """callback_data: kn_today"""
    await update.callback_query.answer()
    context.user_data["kn_date"] = jdatetime.date.today().strftime("%Y/%m/%d")
    msg = update.callback_query.message
    return await _save_and_preview(msg, context)


async def _save_and_preview(msg, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    ذخیرهٔ پیش‌نویس (draft) در DB — در این لحظه id ساخته می‌شود؛
    عکس‌ها به پوشهٔ نهایی منتقل می‌شوند و پیش‌نمایش DANA نشان داده می‌شود.
    """
    try:
        telegram_id = msg.chat.id if msg.chat else None

        user = get_user_by_telegram_id(telegram_id) if telegram_id else None
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
            telegram_id=telegram_id,
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
        await msg.reply_text(text, parse_mode="Markdown", reply_markup=kb)
        return KN_PREVIEW

    except Exception:
        logger.exception("خطا در _save_and_preview دانش")
        await msg.reply_text("❌ خطایی رخ داد.")
        return ConversationHandler.END


async def kn_finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
            await query.edit_message_text("⚠️ رکورد یافت نشد.", reply_markup=back_to_main_keyboard())
            return ConversationHandler.END

        entry = get_knowledge_entry_by_id(knowledge_id)
        if not entry:
            await query.edit_message_text("⚠️ رکورد یافت نشد.", reply_markup=back_to_main_keyboard())
            return ConversationHandler.END

        from engine.knowledge_numbering import generate_knowledge_number

        number = generate_knowledge_number(entry["project_id"])

        # بازسازی گزارش با شمارهٔ نهایی (گزارشِ ذخیره‌شده در مرحلهٔ پیش‌نمایش)
        report = context.user_data.get("kn_report")
        if not report:
            await query.edit_message_text("⚠️ گزارش یافت نشد.", reply_markup=back_to_main_keyboard())
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
            f"🔢 شمارهٔ ثبت: `{number}`\n\n"
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
                        document=InputFile(fh, filename=f"{number}.pdf"),
                        caption=f"📄 پیش‌نویس DANA (PDF) — {number}",
                    )
            except Exception:
                logger.exception("ارسال PDF دانش ناموفق")
        if docx_path and os.path.isfile(docx_path):
            try:
                with open(docx_path, "rb") as fh:
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=InputFile(fh, filename=f"{number}.docx"),
                        caption=f"📝 پیش‌نویس DANA (Word) — {number}",
                    )
            except Exception:
                logger.exception("ارسال DOCX دانش ناموفق")

        # اگر پیش‌نویس طولانی بود، در یک پیام جدا هم ارسال می‌شود
        if len(final_text) > 3500:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=draft,
                    parse_mode="Markdown",
                )
            except Exception:
                logger.exception("ارسال پیش‌نویس DANA در پیام جدا ناموفق بود")

        return ConversationHandler.END

    except Exception:
        logger.exception("خطا در kn_finish")
        await update.callback_query.answer("❌ خطایی رخ داد.", show_alert=True)
        return ConversationHandler.END


# ══════════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════
# مصاحبه با AI (روش۲) — فاز۳h
# ══════════════════════════════════════════════════════════════════════════════

def _create_interview_entry(context: ContextTypes.DEFAULT_TYPE, user: dict) -> int:
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
    context: ContextTypes.DEFAULT_TYPE,
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


async def kn_interview_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """callback_data: kn_interview:start — شروع مصاحبه + اولین سؤال AI."""
    try:
        query = update.callback_query
        await query.answer()
        telegram_id = update.effective_user.id
        user = get_user_by_telegram_id(telegram_id) if telegram_id else None
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
        await _ask_first_interview_question(context, query.edit_message_text if False else query)
        # `query.edit_message_text` فراخوانی شد در _ask_first_interview_question
        return KN_INTERVIEW_LOOP
    except Exception:
        logger.exception("خطا در kn_interview_start")
        return ConversationHandler.END


async def kn_interview_loop_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت پیام متنی در حلقهٔ مصاحبه."""
    from engine.knowledge_interview import interview_next_turn

    try:
        user_text = _input_text(update, context).strip()
        if not user_text:
            await update.message.reply_text("❌ متن خالی است. پاسخ خود را بنویسید:")
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
            await update.message.reply_text(
                f"✅ مصاحبه تمام شد.\n\n📋 *خلاصه*: {summary}\n\n"
                "▶️ ادامه برای ساخت فرم نهایی DANA...",
                parse_mode="Markdown",
            )
            return await _final_assemble_and_preview(context, update.message)

        ask = result.get("ask")
        if not ask:
            # هیچ سؤالی ساخته نشد — احتمالاً LLM به‌کل شکست خورده؛ به‌جای پیام
            # بی‌هدف «ادامه بدهید»، به کاربر اطلاع بده و بگذار ادامه دهد/خاتمه دهد.
            await update.message.reply_text(
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

        await update.message.reply_text(
            f"🤖 *AI:* {ask}",
            parse_mode="Markdown",
            reply_markup=_interview_continue_keyboard(),
        )
        return KN_INTERVIEW_LOOP

    except Exception:
        logger.exception("خطا در kn_interview_loop_text")
        await update.message.reply_text("❌ خطایی رخ داد.")
        return KN_INTERVIEW_LOOP


async def kn_interview_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
    context: ContextTypes.DEFAULT_TYPE,
    msg,
) -> int:
    """
    پاس نهایی polish با AI + ساخت گزارش + ذخیره draft + رفتن به KN_PREVIEW.
    """
    try:
        from engine.knowledge_interview import polish_dana_draft

        await msg.reply_text("🔄 در حال ساخت فرم نهایی DANA...")
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

        await msg.reply_text(
            f"✅ فرم DANA آماده شد.\n\n📋 *پیش‌نمایش:*\n\n{draft}",
            parse_mode="Markdown",
            reply_markup=_preview_keyboard(),
        )
        return KN_PREVIEW

    except Exception:
        logger.exception("خطا در _final_assemble_and_preview")
        await msg.reply_text("❌ خطا در ساخت فرم نهایی.")
        return ConversationHandler.END


async def kn_final_assemble_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """callback_data: kn_final:done — placeholder اگر بعداً لازم شد."""
    await update.callback_query.answer()
    return KN_PREVIEW


# ══════════════════════════════════════════════════════════════════════════════
# تنظیمات سازمانی (درخت/کمیته/بذر/همکاران/هشتگ/محدوده) — فاز۳i
# ══════════════════════════════════════════════════════════════════════════════

def _current_org_display(context: ContextTypes.DEFAULT_TYPE, knowledge_type: str) -> dict:
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


async def kn_org_tree(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
            f"وضعیت فعلی: `{tree_display}`\n\n"
            "روش انتخاب را مشخص کنید:",
            parse_mode="Markdown",
            reply_markup=kb,
        )
        return KN_TREE
    except Exception:
        logger.exception("خطا در kn_org_tree")
        return KN_ORG_META


async def kn_org_committee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع ویرایش کمیته تخصصی."""
    try:
        query = update.callback_query
        await query.answer()
        context.user_data["kn_org_pending_field"] = "committee"
        current = (context.user_data.get("kn_org_metadata") or {}).get("committee", "")
        await query.edit_message_text(
            f"👥 *کمیته تخصصی پیشنهادی*\n\n"
            f"مقدار فعلی: `{current or 'خالی'}`\n\n"
            "مقدار جدید را بنویسید (یا /skip برای رد کردن):",
            parse_mode="Markdown",
        )
        return KN_ORG_META
    except Exception:
        logger.exception("خطا در kn_org_committee")
        return KN_ORG_META


async def kn_org_seed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        context.user_data["kn_org_pending_field"] = "seed"
        current = (context.user_data.get("kn_org_metadata") or {}).get("seed", "")
        await query.edit_message_text(
            f"💡 *بذر پیشنهاد*\n\n"
            f"مقدار فعلی: `{current or 'خالی'}`\n\n"
            "ایده اولیه از کجا آمد؟ بنویسید:",
            parse_mode="Markdown",
        )
        return KN_ORG_META
    except Exception:
        logger.exception("خطا در kn_org_seed")
        return KN_ORG_META


async def kn_org_colleagues(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        context.user_data["kn_org_pending_field"] = "colleagues"
        current = (context.user_data.get("kn_org_metadata") or {}).get("colleagues", "")
        await query.edit_message_text(
            f"🤝 *همکاران درگیر*\n\n"
            f"مقدار فعلی: `{current or 'خالی'}`\n\n"
            "نام همکاران (با کاما جدا کنید) را بنویسید:",
            parse_mode="Markdown",
        )
        return KN_ORG_META
    except Exception:
        logger.exception("خطا در kn_org_colleagues")
        return KN_ORG_META


async def kn_org_hashtags(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        context.user_data["kn_org_pending_field"] = "hashtags"
        current = " ".join("#" + h for h in (context.user_data.get("kn_hashtags") or []))
        await query.edit_message_text(
            f"#️⃣ *هشتگها*\n\n"
            f"مقدار فعلی: `{current or 'خالی'}`\n\n"
            "هشتگهای جدید را با فاصله بنویسید (مثلاً: `#ایمنی #جوشکاری`):",
            parse_mode="Markdown",
        )
        return KN_ORG_META
    except Exception:
        logger.exception("خطا در kn_org_hashtags")
        return KN_ORG_META


async def kn_org_scope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        query = update.callback_query
        await query.answer()
        context.user_data["kn_org_pending_field"] = "scope"
        current = (context.user_data.get("kn_org_metadata") or {}).get("scope", "")
        await query.edit_message_text(
            f"🏢 *محدوده سازمانی*\n\n"
            f"مقدار فعلی: `{current or 'خالی'}`\n\n"
            "این دانش به کدام بخشهای سازمان مربوط است؟",
            parse_mode="Markdown",
        )
        return KN_ORG_META
    except Exception:
        logger.exception("خطا در kn_org_scope")
        return KN_ORG_META


async def kn_org_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت متن برای فیلد در حال ویرایش."""
    try:
        field = context.user_data.get("kn_org_pending_field")
        if not field:
            # اگر pending نیست، منوی org_meta را دوباره نشان بده
            return await _show_org_menu(update, context)

        text = (update.message.text or "").strip()
        if not text:
            await update.message.reply_text("❌ متن خالی است. دوباره بنویسید یا /skip:")
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
        await update.message.reply_text(f"✓ `{field}` به‌روز شد.")
        return await _show_org_menu(update, context)

    except Exception:
        logger.exception("خطا در kn_org_text_input")
        return KN_ORG_META


async def _show_org_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
    else:
        await update.message.reply_text(
            "\n".join(text_lines),
            parse_mode="Markdown",
            reply_markup=kb,
        )
    return KN_ORG_META


async def kn_org_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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


async def kn_org_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """رد کردن کل تنظیمات سازمانی → preview بدون org_meta."""
    try:
        query = update.callback_query
        await query.answer()
        await kn_org_done.callback(update, context)  # نوع: مستقیم به preview
        return KN_PREVIEW
    except Exception:
        logger.exception("خطا در kn_org_skip")
        return KN_ORG_META


# ══════════════════════════════════════════════════════════════════════════════
# درخت دانش — drill-down + AI suggestion + type + skip
# ══════════════════════════════════════════════════════════════════════════════

async def kn_tree_ai(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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


async def kn_tree_ai_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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


async def kn_tree_nav(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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


async def kn_tree_nav_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """یک سطح به عقب."""
    try:
        query = update.callback_query
        await query.answer()
        current_path = context.user_data.get("kn_tree_current_path") or []
        if not current_path:
            # بازگشت به منوی انتخاب روش
            return await kn_org_tree.callback(query, context)
        current_path = current_path[:-1]
        context.user_data["kn_tree_current_path"] = current_path

        from engine.knowledge_tree import get_children as _gc
        new_children = _gc(current_path)
        if not new_children:
            return await kn_org_tree.callback(query, context)
        kb = _tree_drill_keyboard(len(current_path), current_path, new_children)
        await query.edit_message_text(
            f"🌳 مسیر: {' > '.join(current_path)}\n\nفرزندان را انتخاب کنید:",
            reply_markup=kb,
        )
        return KN_TREE
    except Exception:
        logger.exception("خطا در kn_tree_nav_back")
        return KN_TREE


async def kn_tree_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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


async def kn_tree_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع تایپ مسیر کامل."""
    try:
        query = update.callback_query
        await query.answer()
        context.user_data["kn_tree_typing"] = True
        await query.edit_message_text(
            "✏️ *تایپ مسیر کامل*\n\n"
            "مسیر را با `>` جدا کنید، از ریشه شروع کنید.\n"
            "مثال:\n`MAPNA Development > HSE Management > Safety`\n\n"
            "اگر اشتباه است، /skip کنید.",
            parse_mode="Markdown",
        )
        return KN_TREE
    except Exception:
        logger.exception("خطا در kn_tree_type")
        return KN_TREE


async def kn_tree_type_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """پایان تایپ مسیر."""
    try:
        if not context.user_data.get("kn_tree_typing"):
            # پیام در حالت دیگری — نادیده بگیر
            return KN_ORG_META

        text = (update.message.text or "").strip()
        context.user_data.pop("kn_tree_typing", None)
        if not text or text.startswith("/skip"):
            await update.message.reply_text("↩️ رد شد.")
            return await _show_org_menu(update, context)

        path = [p.strip() for p in text.split(">") if p.strip()]
        from engine.knowledge_tree import validate_path
        if not validate_path(path):
            await update.message.reply_text(
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

        await update.message.reply_text(f"✓ مسیر ثبت شد: {' > '.join(path)}")
        return await _show_org_menu(update, context)
    except Exception:
        logger.exception("خطا در kn_tree_type_done")
        return KN_TREE


async def kn_tree_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """رد کردن انتخاب درخت."""
    try:
        query = update.callback_query
        await query.answer()
        return await _show_org_menu(update, context)
    except Exception:
        logger.exception("خطا در kn_tree_skip")
        return KN_TREE


# ══════════════════════════════════════════════════════════════════════════════
# ویرایش فیلد از preview — فاز۳j
# ══════════════════════════════════════════════════════════════════════════════

def _editable_fields(knowledge_type: str) -> list[str]:
    """فیلدهای قابل ویرایش برای یک نوع دانش (به‌همراه impact_type/subtype دکمهای)."""
    from engine.knowledge_ai import BUTTON_FIELDS, FIELD_SCHEMAS
    keys = list(FIELD_SCHEMAS.get(knowledge_type, {}).keys())
    btn_keys = list(BUTTON_FIELDS.get(knowledge_type, {}).keys())
    return keys + btn_keys


async def kn_preview_to_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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


async def kn_edit_to_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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


async def kn_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
            f"مقدار فعلی: `{current or 'خالی'}`\n\n"
            "مقدار جدید را بنویسید (یا /skip برای رد کردن):",
            parse_mode="Markdown",
        )
        return KN_FIELD_EDIT
    except Exception:
        logger.exception("خطا در kn_edit_field")
        return KN_FIELD_EDIT


async def kn_edit_btn_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
            f"✓ `{key}` به `{value}` تغییر کرد.\n\nروی فیلد دیگری بزنید یا برگردید.",
            reply_markup=kb,
        )
        return KN_FIELD_EDIT
    except Exception:
        logger.exception("خطا در kn_edit_btn_choice")
        return KN_FIELD_EDIT


async def kn_edit_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """متن جدید برای فیلد در حال ویرایش."""
    try:
        key = context.user_data.get("kn_edit_pending_key")
        if not key:
            return KN_FIELD_EDIT
        text = (update.message.text or "").strip()
        if not text or text.startswith("/skip"):
            context.user_data.pop("kn_edit_pending_key", None)
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
        await update.message.reply_text(
            f"✓ `{key}` به‌روز شد.\n\nروی فیلد دیگری بزنید یا برگردید.",
            reply_markup=kb,
        )
        return KN_FIELD_EDIT
    except Exception:
        logger.exception("خطا در kn_edit_text_input")
        return KN_FIELD_EDIT


# ══════════════════════════════════════════════════════════════════════════════
# Placeholder برای kn_edit_back قدیمی (دیگر استفاده نمیشود — برای سازگاری)
# ══════════════════════════════════════════════════════════════════════════════

async def kn_edit_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """سازگاری با کد قدیمی — به KN_PREVIEW برمیگردد."""
    return await kn_edit_to_preview(update, context)


async def kn_photos_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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

async def _cancel_knowledge_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
            ],
        },
        fallbacks=[
            CommandHandler("cancel", _cancel_knowledge_conv),
            CommandHandler("start", _cancel_knowledge_conv),
        ],
        per_message=False,
        name="knowledge_registration",
        persistent=False,
    )
