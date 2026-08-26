"""
لایه سازگاری PTB-مانند برای python-bale-bot.

هدف: انتقال سه‌گانه‌ی هندلرهای پروژه (registration / observations / knowledge)
با حداقل تغییر — با شبیه‌سازی رفتار python-telegram-bot روی بله:

  - Update/Message/CallbackQuery با effective_user/effective_message
  - reply_text / edit_message_text با پذیرش parse_mode (مارک‌داون حذف می‌شود — بله پشتیبانی نمی‌کند)
  - query.answer()  (در API بله وجود ندارد — فقط alert به شکل پیام ارسال می‌شود)
  - InlineKeyboardMarkup([[btn]]) و ReplyKeyboardMarkup/ReplyKeyboardRemove
  - ConversationHandler با entry_points/states/fallbacks + context.user_data
  - filters.TEXT & ~filters.COMMAND و ...

این ماژول هیچ منطق کسب‌وکاری ندارد.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import bale

logger = logging.getLogger(__name__)
from bale import (
    Bot,
    InlineKeyboardButton as _BaleInlineButton,
    InlineKeyboardMarkup as _BaleInlineMarkup,
    MenuKeyboardButton as _BaleMenuButton,
    MenuKeyboardMarkup as _BaleMenuMarkup,
    InputFile,
)


# ══════════════════════════════════════════════════════════════════════════════
# مارک‌داون — بله parse_mode ندارد؛ فرمت‌ها را تمیز می‌کنیم
# ══════════════════════════════════════════════════════════════════════════════

_MD_PATTERNS = [
    re.compile(r"\*([^*\n]+)\*"),
    re.compile(r"`([^`\n]+)`"),
    re.compile(r"_([^_\n]+)_"),
]


def strip_markdown(text: str) -> str:
    """حذف مارکرهای *bold*، _italic_ و `code` — برای نمایش ساده در بله."""
    if not text:
        return text
    out = str(text)
    for pat in _MD_PATTERNS:
        out = pat.sub(r"\1", out)
    # مارکرهای تک‌مانده
    out = out.replace("*", "").replace("`", "")
    return out


# ══════════════════════════════════════════════════════════════════════════════
# کیبوردها — رابط PTB، خروجی bale
# ══════════════════════════════════════════════════════════════════════════════

class KeyboardButton:
    """دکمه‌ی reply keyboard — معادل telegram.KeyboardButton."""

    def __init__(self, text: str, request_contact: bool = False,
                 request_location: bool = False) -> None:
        self.text = text
        self.request_contact = request_contact
        self.request_location = request_location


class ReplyKeyboardMarkup:
    """معادل telegram.ReplyKeyboardMarkup → bale.MenuKeyboardMarkup."""

    def __init__(self, keyboard, resize_keyboard: bool = False,
                 one_time_keyboard: bool = False) -> None:
        self.keyboard = [list(row) for row in keyboard]
        self.resize_keyboard = resize_keyboard
        self.one_time_keyboard = one_time_keyboard

    def to_bale(self) -> _BaleMenuMarkup:
        markup = _BaleMenuMarkup()
        for row_i, row in enumerate(self.keyboard, start=1):
            for btn in row:
                if isinstance(btn, KeyboardButton):
                    markup.add(
                        _BaleMenuButton(
                            btn.text,
                            request_contact=bool(btn.request_contact),
                            request_location=bool(btn.request_location),
                        ),
                        row=row_i,
                    )
                else:
                    markup.add(_BaleMenuButton(str(btn)), row=row_i)
        return markup


class ReplyKeyboardRemove:
    """حذف menu keyboard — با ارسال منوی خالی."""

    def to_bale(self) -> _BaleMenuMarkup:
        return _BaleMenuMarkup()


class _BaleWebAppButton(bale.InlineKeyboardButton):
    """دکمهٔ مینی‌اپ — کتابخانه web_app ندارد؛ مستقیم در خروجی JSON تزریق می‌شود."""

    def __init__(self, text: str, web_app_url: str):
        super().__init__(text=text)
        self._webapp_url = web_app_url

    def to_dict(self) -> dict:
        return {"text": self.text, "web_app": {"url": self._webapp_url}}


class _BaleCopyTextButton(bale.InlineKeyboardButton):
    """دکمهٔ کپی متن (تا ~۲۵۶ کاراکتر) — مشابه web_app با تزریق JSON."""

    def __init__(self, text: str, copy_text: str):
        super().__init__(text=text)
        self._copy_text = copy_text

    def to_dict(self) -> dict:
        return {"text": self.text, "copy_text": {"text": self._copy_text}}


class InlineKeyboardButton:
    """معادل telegram.InlineKeyboardButton."""

    def __init__(self, text: str, callback_data: str | None = None,
                 url: str | None = None, web_app: str | None = None,
                 copy_text: str | None = None) -> None:
        self.text = text
        self.callback_data = callback_data
        self.url = url
        self.web_app = web_app
        self.copy_text = copy_text

    def to_bale(self) -> bale.InlineKeyboardButton:
        if self.web_app:
            return _BaleWebAppButton(text=self.text, web_app_url=self.web_app)
        if self.copy_text:
            return _BaleCopyTextButton(text=self.text, copy_text=self.copy_text)
        return _BaleInlineButton(
            text=self.text, callback_data=self.callback_data, url=self.url,
        )


class InlineKeyboardMarkup:
    """معادل telegram.InlineKeyboardMarkup — ورودی لیست تو در تو."""

    def __init__(self, keyboard=None) -> None:
        self.keyboard = [list(row) for row in keyboard] if keyboard else []

    def to_bale(self) -> _BaleInlineMarkup:
        markup = _BaleInlineMarkup()
        for row_i, row in enumerate(self.keyboard, start=1):
            for btn in row:
                markup.add(btn.to_bale(), row=row_i)
        return markup


def convert_markup(markup) -> Any | None:
    """هر markup سازگار PTB را به شیء بله تبدیل می‌کند."""
    if markup is None:
        return None
    if isinstance(markup, (InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove)):
        return markup.to_bale()
    if isinstance(markup, (_BaleInlineMarkup, _BaleMenuMarkup)):
        return markup
    raise TypeError(f"نوع reply_markup پشتیبانی نمی‌شود: {type(markup)!r}")


# ══════════════════════════════════════════════════════════════════════════════
# فیلترها — شبیه telegram.ext.filters
# ══════════════════════════════════════════════════════════════════════════════

class MessageFilter:
    """پایه‌ی فیلترها — زیرکلاس‌ها filter() را پیاده می‌کنند."""

    def filter(self, message: "_Msg") -> bool:  # pragma: no cover
        raise NotImplementedError

    def check(self, message: "_Msg") -> bool:
        try:
            return bool(self.filter(message))
        except Exception:
            return False

    def __and__(self, other: "MessageFilter") -> "MessageFilter":
        return _AndFilter(self, other)

    def __invert__(self) -> "MessageFilter":
        return _NotFilter(self)


class _AndFilter(MessageFilter):
    def __init__(self, a: MessageFilter, b: MessageFilter) -> None:
        self.a, self.b = a, b

    def filter(self, message) -> bool:
        return self.a.check(message) and self.b.check(message)


class _NotFilter(MessageFilter):
    def __init__(self, a: MessageFilter) -> None:
        self.a = a

    def filter(self, message) -> bool:
        return not self.a.check(message)


class _Text(MessageFilter):
    def filter(self, m) -> bool:
        return bool(m.text)


class _Command(MessageFilter):
    def filter(self, m) -> bool:
        t = m.text or ""
        return bool(t) and t.startswith("/")


class _Contact(MessageFilter):
    def filter(self, m) -> bool:
        return bool(m.contact)


class _Photo(MessageFilter):
    def filter(self, m) -> bool:
        return bool(m.photo)


class _DocumentAll(MessageFilter):
    def filter(self, m) -> bool:
        return bool(m.document)


class _DocumentNamespace:
    ALL = _DocumentAll()


class _FiltersNamespace:
    TEXT = _Text()
    COMMAND = _Command()
    CONTACT = _Contact()
    PHOTO = _Photo()
    Document = _DocumentNamespace()


filters = _FiltersNamespace()


# ══════════════════════════════════════════════════════════════════════════════
# Wrapper ها — Message / CallbackQuery / Update
# ══════════════════════════════════════════════════════════════════════════════

class _Msg:
    """Wrapper بله Message با رابط نزدیک به telegram.Message."""

    def __init__(self, raw: bale.Message, bot: Bot) -> None:
        self._raw = raw
        self._bot = bot
        self.message_id = raw.message_id
        self.chat_id = raw.chat_id
        self.chat = raw.chat
        self.from_user = raw.from_user
        self.text = raw.text
        self.caption = raw.caption
        # نسخه ۲.۵ پیپي «photos» دارد؛ master گیت‌هاب «photo» — هر دو پوشش داده می‌شود
        self.photo = getattr(raw, "photos", None) or getattr(raw, "photo", None)
        # در bale 2.5 کلاس Voice جدا وجود ندارد؛ ویس به‌صورت audio/document می‌آید
        self.voice = getattr(raw, "voice", None)
        self.audio = raw.audio
        self.document = raw.document
        self.video = raw.video
        self.animation = raw.animation
        self.contact = raw.contact
        self.location = raw.location
        # سازگاری با کد تلگرامی — در بله video_note وجود ندارد
        self.video_note = None

    @property
    def author(self):  # noqa: ANN201 — همان bale.User
        return self._raw.from_user

    @property
    def effective_attachment(self):  # noqa: ANN201
        return self._raw.attachment

    async def reply_text(self, text: str, parse_mode: str | None = None,
                         reply_markup=None, **_ignored) -> "_Msg":
        payload = strip_markdown(text)
        msg = await self._bot.send_message(
            self.chat_id, payload, components=convert_markup(reply_markup),
        )
        return _Msg(msg, self._bot)

    async def reply_document(self, document, caption: str | None = None,
                             filename: str | None = None, **_ignored) -> "_Msg":
        payload = strip_markdown(caption) if caption else None
        file_input = _prepare_file_input(document, filename)
        msg = await self._bot.send_document(
            self.chat_id, file_input, caption=payload,
        )
        return _Msg(msg, self._bot)

    async def reply_photo(self, photo, caption: str | None = None,
                          filename: str | None = None, **_ignored) -> "_Msg":
        payload = strip_markdown(caption) if caption else None
        file_input = _prepare_file_input(photo, filename)
        msg = await self._bot.send_photo(self.chat_id, file_input, caption=payload)
        return _Msg(msg, self._bot)


def _prepare_file_input(document, filename: str | None = None):
    """
    مسیر فایل / bytes / BufferedReader را به InputFile بله تبدیل می‌کند.
    (ارسال با مسیر مستقیم در این نسخه کتابخونه شکننده است — خودمان bytes می‌خوانیم)
    """
    if isinstance(document, InputFile):
        return document
    if isinstance(document, (bytes, bytearray)):
        return InputFile(bytes(document), file_name=filename)
    # مسیر فایل
    with open(str(document), "rb") as fh:
        data = fh.read()
    name = filename
    if not name:
        import os
        name = os.path.basename(str(document))
    return InputFile(data, file_name=name)


class _Cbq:
    """Wrapper بله CallbackQuery با رابط نزدیک به telegram.CallbackQuery."""

    def __init__(self, raw: bale.CallbackQuery, bot: Bot) -> None:
        self._raw = raw
        self._bot = bot
        self.id = raw.id
        self.data = raw.data
        self.from_user = raw.from_user
        self.message = _Msg(raw.message, bot) if raw.message else None

    @property
    def user(self):  # noqa: ANN201
        return self._raw.from_user

    async def answer(self, text: str = "", show_alert: bool = False) -> None:
        """API بله answerCallbackQuery ندارد.
        alert های متنی به‌صورت پیام معمولی برای کاربر ارسال می‌شوند."""
        if text and show_alert and self.message:
            try:
                await self.message.reply_text(strip_markdown(text))
            except Exception:
                pass

    async def edit_message_text(self, text: str, parse_mode: str | None = None,
                                reply_markup=None, **_ignored) -> None:
        payload = strip_markdown(text)
        markup = convert_markup(reply_markup)
        if not self.message:
            raise RuntimeError("پیام دکمه برای ویرایش یافت نشد")
        try:
            await self._bot.edit_message(
                self.message.chat_id, self.message.message_id, payload,
                components=markup,
            )
        except Exception as exc:
            # پیام ممکن است حذف شده باشد (مثلاً پاکسازی پرامپت قدیمی) —
            # به‌جای خطا، همان محتوا به‌صورت پیام تازه ارسال می‌شود.
            logger.warning("edit_message ناموفق (%s) — fallback به پیام جدید", exc)
            try:
                await self.message.reply_text(payload, reply_markup=reply_markup)
            except Exception:
                logger.exception("fallback ارسال پیام جدید نیز ناموفق بود")


class _Update:
    """Wrapper بله Update با رابط telegram.Update."""

    def __init__(self, raw_update: bale.Update, bot: Bot) -> None:
        self.update_id = raw_update.update_id
        self._bot = bot
        self.message = _Msg(raw_update.message, bot) if getattr(raw_update, "message", None) else None
        self.edited_message = (
            _Msg(raw_update.edited_message, bot)
            if getattr(raw_update, "edited_message", None) else None
        )
        self.callback_query = (
            _Cbq(raw_update.callback_query, bot)
            if getattr(raw_update, "callback_query", None) else None
        )

    @property
    def effective_user(self):  # noqa: ANN201 — bale.User یا None
        if self.message and self.message.from_user:
            return self.message.from_user
        if self.callback_query:
            return self.callback_query.from_user
        if self.edited_message:
            return self.edited_message.from_user
        return None

    @property
    def effective_chat(self):  # noqa: ANN201
        target = self.effective_message
        return target.chat if target else None

    @property
    def effective_message(self) -> Optional[_Msg]:
        return self.message or self.edited_message or (
            self.callback_query.message if self.callback_query else None
        )


class Context:
    """معادل سبک telegram.ext.ContextTypes.DEFAULT_TYPE."""

    def __init__(self, bot: Bot, user_data: Dict[str, Any]) -> None:
        self.bot = bot
        self.user_data = user_data
        self.chat_data = user_data  # در چت خصوصی یکسان است
        self.error = None


# ══════════════════════════════════════════════════════════════════════════════
# مدیریت پرامپت‌ها — پیام قبلی ربات قبل از نمایش پیام جدید حذف شود
# ══════════════════════════════════════════════════════════════════════════════

def track_prompt(context: Context, msg, key: str = "_bot_prompt") -> None:
    """شناسهٔ آخرین پیام پرامپت ربات را برای حذف بعدی ذخیره میکند."""
    try:
        context.user_data[key] = {"chat": msg.chat_id, "id": msg.message_id}
    except Exception:
        pass


async def delete_tracked(context: Context, key: str = "_bot_prompt") -> None:
    """پیام پرامپت ذخیره‌شده را پاک میکند (اگر باشد؛ خطا نادیده گرفته میشود)."""
    info = context.user_data.pop(key, None)
    if not info:
        return
    try:
        await context.bot.delete_message(info["chat"], info["id"])
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# هندلرها — امضای PTB
# ══════════════════════════════════════════════════════════════════════════════

class MessageHandler:
    """telegram.ext.MessageHandler(filters, callback)."""

    def __init__(self, filt: Optional[MessageFilter], callback) -> None:
        self.filter = filt
        self.callback = callback

    def check(self, update: _Update) -> bool:
        if update.message is None:
            return False
        if update.edited_message is not None and update.message is None:
            return False
        if self.filter is None:
            return True
        return self.filter.check(update.message)


class CommandHandler:
    """telegram.ext.CommandHandler(command, callback)."""

    def __init__(self, command: str, callback) -> None:
        self.command = command.lower()
        self.callback = callback

    def check(self, update: _Update) -> bool:
        msg = update.message
        if msg is None or not msg.text:
            return False
        first = msg.text.strip().split(" ", 1)[0].lower()
        # «/start@botname» هم پوشش داده شود (مطابق PTB)
        base = first.split("@", 1)[0]
        return base == f"/{self.command}"


class CallbackQueryHandler:
    """telegram.ext.CallbackQueryHandler(callback, pattern=None).
    توجه: در کتابخونه‌ی بله CallbackQueryHandler بدون check کار نمی‌کند؛
    این پیاده‌سازی مشابه PTB خودمان pattern را با re.match بررسی می‌کنیم."""

    def __init__(self, callback, pattern: str | None = None) -> None:
        self.pattern = re.compile(pattern) if pattern else None
        self.callback = callback

    def check(self, update: _Update) -> bool:
        q = update.callback_query
        if q is None:
            return False
        if self.pattern is None:
            return True
        return bool(self.pattern.match(q.data or ""))


class TypeHandler:
    """معادل ساده telegram.ext.TypeHandler — هر update را می‌گیرد."""

    def __init__(self, update_type: type, callback) -> None:  # noqa: ARG002
        self.callback = callback

    def check(self, update: _Update) -> bool:
        return True


# ══════════════════════════════════════════════════════════════════════════════
# ConversationHandler — شبیه‌سازی PTB
# ══════════════════════════════════════════════════════════════════════════════

_NO_MATCH = object()


class ConversationHandler:
    """شبیه‌ساز telegram.ext.ConversationHandler (per_message=False, per_chat/user).

    route() توسط Dispatcher صدا زده می‌شود:
      - اگر کاربر داخل این گفتگو باشد → state handlers + fallbacks
      - در غیر این صورت → entry_points
    خروجی: (consumed: bool, new_state: int | None)
      new_state None یعنی «همان state قبلی بماند» (مطابق PTB).
    """

    END: int = -1

    def __init__(self, entry_points: list, states: Dict[int, list],
                 fallbacks: list, per_message: bool = False, name: str | None = None,
                 persistent: bool = False, allow_reentry: bool = False) -> None:
        self.entry_points = entry_points
        self.states = states
        self.fallbacks = fallbacks
        self.per_message = per_message
        self.name = name
        self.persistent = persistent  # در بله استفاده نمی‌شود — سازگاری امضا
        self.allow_reentry = allow_reentry

    async def route(self, update: _Update, context: Context,
                    state: Optional[int]) -> Tuple[bool, Optional[int]]:
        if state is not None and state != self.END:
            entries: List[Any] = list(self.states.get(state, [])) + list(self.fallbacks)
            if self.allow_reentry:
                # مطابق PTB: در حالت فعال هم entry_points بعد از states/fallbacks بررسی شوند
                entries += list(self.entry_points)
        elif state is not None and state == self.END:
            # پایان رسمی — فقط entry مجدد (allow_reentry) یا fallback
            entries = (list(self.entry_points) if self.allow_reentry else []) \
                + list(self.fallbacks)
        else:
            entries = list(self.entry_points)

        for entry in entries:
            if not entry.check(update):
                continue
            result = await entry.callback(update, context)
            if result is None:
                return True, state  # بدون تغییر state
            return True, result
        return False, state


# ══════════════════════════════════════════════════════════════════════════════
# Dispatcher — جایگزین Application + groups در main.py
# ══════════════════════════════════════════════════════════════════════════════

class Dispatcher:
    """
    ترتیب پردازش هر update (مطابق گروه‌بندی main.py تلگرام):
      ۱. busy_guard (group 0 — همیشه اجرا می‌شود)
      ۲. ConversationHandler ها به ترتیب ثبت — اولین موردی که می‌گیرد برنده است
      ۳. هندلرهای مستقل (menu:main ، /help و ...)
      ۴. fallback های ناشناخته
    """

    def __init__(self, bot: Bot, busy_guard: Optional[Callable] = None) -> None:
        self.bot = bot
        self.busy_guard = busy_guard
        self.conversations: List[ConversationHandler] = []
        self.standalone: List[Any] = []
        self.unknown_fallbacks: List[Any] = []
        self.error_handler: Optional[Callable] = None
        self._states: Dict[int, Dict[int, int]] = defaultdict(dict)
        self._user_data: Dict[int, Dict[str, Any]] = defaultdict(dict)

    def add_conversation(self, conv: ConversationHandler) -> None:
        self.conversations.append(conv)

    def add_standalone(self, entry) -> None:
        self.standalone.append(entry)

    def add_unknown_fallback(self, entry) -> None:
        self.unknown_fallbacks.append(entry)

    def set_error_handler(self, handler: Callable) -> None:
        self.error_handler = handler

    def _context_for(self, user_key: int) -> Context:
        return Context(self.bot, self._user_data[user_key])

    async def dispatch(self, raw_update: bale.Update) -> None:
        update = _Update(raw_update, self.bot)
        user = update.effective_user
        user_key = user.id if user else (
            update.effective_chat.id if update.effective_chat else 0
        )
        context = self._context_for(user_key)

        try:
            # ── group 0: قفل «در حال پردازش» ──
            if self.busy_guard:
                await self.busy_guard(update, context)

            # ── group 1: ConversationHandler ها ──
            conv_states = self._states[user_key]
            for index, conv in enumerate(self.conversations):
                state = conv_states.get(index)
                consumed, new_state = await conv.route(update, context, state)
                if not consumed:
                    continue
                if new_state is None or new_state == ConversationHandler.END:
                    conv_states.pop(index, None)
                else:
                    conv_states[index] = new_state
                return

            # ── group 1: هندلرهای مستقل ──
            for entry in self.standalone:
                if entry.check(update):
                    # دکمه‌های سراسری (مثل menu:main) یعنی خروج از هر flow فعال
                    self._states.pop(user_key, None)
                    await entry.callback(update, context)
                    return

            # ── fallback ناشناخته ──
            for entry in self.unknown_fallbacks:
                if entry.check(update):
                    await entry.callback(update, context)
                    return

        except Exception as exc:  # noqa: BLE001 — error handler سراسری
            if self.error_handler:
                context.error = exc
                await self.error_handler(update, context)
            else:
                raise


__all__ = (
    "strip_markdown",
    "KeyboardButton", "ReplyKeyboardMarkup", "ReplyKeyboardRemove",
    "InlineKeyboardButton", "InlineKeyboardMarkup",
    "filters", "MessageFilter",
    "MessageHandler", "CommandHandler", "CallbackQueryHandler", "TypeHandler",
    "ConversationHandler", "Context", "Dispatcher",
    "track_prompt", "delete_tracked",
    "_Msg", "_Cbq", "_Update",
)
