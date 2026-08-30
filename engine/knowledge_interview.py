"""
engine/knowledge_interview.py
موتور مصاحبه با AI + پاس polish نهایی + پیشنهاد درخت دانش.

این ماژول سه قابلیت اصلی دارد:
  ۱. interview_next_turn() — یک نوبت مکالمهٔ چندمرحلهای با AI
  ۲. polish_dana_draft()   — پاس نهایی برای ساخت narrative حرفه‌ای
  ۳. suggest_tree_paths()  — پیشنهاد مسیر درخت دانش رسمی

اگر AI در دسترس نباشد (is_ai_enabled() == False)، همهٔ توابع مقادیر خالی/None
برمیگردانند — handler باید به حالت مکانیکی (پرسش دستی یا fallback) برگردد.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from engine.knowledge_ai import (
    BUTTON_FIELDS,
    FIELD_SCHEMAS,
    TYPE_LABELS,
    _call_llm_messages,
    _parse_json_response,
    is_ai_enabled,
)
from engine.knowledge_tree import tree_as_yaml, validate_path

logger = logging.getLogger(__name__)

# بارگذاری واژه‌نامه تخصصی پروژه‌ها
import os as _os

_GLOSSARY_CACHE: dict[str, Any] | None = None


def _get_glossary() -> dict[str, Any]:
    global _GLOSSARY_CACHE
    if _GLOSSARY_CACHE is not None:
        return _GLOSSARY_CACHE
    base_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    glossary_path = _os.path.join(base_dir, "references", "plant_glossary.json")
    if not _os.path.isfile(glossary_path):
        glossary_path = _os.path.join(base_dir, "plant_glossary.json")
    if _os.path.isfile(glossary_path):
        try:
            with open(glossary_path, "r", encoding="utf-8") as fh:
                _GLOSSARY_CACHE = json.load(fh)
                return _GLOSSARY_CACHE
        except Exception:
            logger.exception("خطا در بارگذاری واژه‌نامه اصطلاحات")
    _GLOSSARY_CACHE = {}
    return _GLOSSARY_CACHE


def _extract_relevant_glossary(project_name: str | None) -> str:
    glossary = _get_glossary()
    if not glossary:
        return "واژه‌نامه تخصصی در دسترس نیست."
    terms_list = []
    for item in glossary.get("عمومی صنعت برق", [])[:30]:
        terms_list.append(f"- {item['term_fa']} ({item['term_en']}): {item['description']}")
    p_name = (project_name or "").lower()
    cat = "نیروگاه سیکل ترکیبی"
    if "بخار" in p_name or "حرارتی" in p_name or "بویلر" in p_name:
        cat = "نیروگاه بخاری/حرارتی مقیاس بزرگ"
    elif "دیزل" in p_name or "موتور" in p_name or "chp" in p_name or "مقیاس کوچک" in p_name:
        cat = "نیروگاه مقیاس کوچک"
    elif "آب‌شیرین" in p_name or "ro" in p_name:
        cat = "تأسیسات آب‌شیرین‌کن"
    elif "پست" in p_name or "انتقال" in p_name:
        cat = "پست‌های انتقال و فوق‌توزیع برق"
    elif "خورشید" in p_name:
        cat = "نیروگاه خورشیدی"
    elif "باد" in p_name:
        cat = "نیروگاه بادی"
    for item in glossary.get(cat, [])[:40]:
        terms_list.append(f"- {item['term_fa']} ({item['term_en']}): {item['description']}")
    return "\n".join(terms_list)

# ══════════════════════════════════════════════════════════════════════════════
# فریمورک‌های مصاحبه — ترتیب پیشنهادی سؤال‌ها برای هر نوع
# ══════════════════════════════════════════════════════════════════════════════
# کلیدهایی که دکمهای هستند (نه متنی) در مصاحبه، در انتهای فریمورک میآیند تا
# اپراتور بعد از پر کردن محتوا، نوع/تاثیر را انتخاب کند.
INTERVIEW_FRAMEWORKS: dict[str, list[str]] = {
    "lesson": [
        "context", "status", "problem", "cause", "action",
        "result", "lesson", "transferability", "recommendation",
    ],
    "suggestion": [
        "current_state", "problem", "proposal", "expected_impact",
        "colleagues",
        # impact_type در انتها بهصورت دکمهای پرسیده میشود
        "impact_type",
    ],
    "explicit": [
        "subject", "description", "scope", "colleagues",
        # subtype در انتها بهصورت دکمهای پرسیده میشود
        "subtype",
    ],
}

_MAX_JSON_RETRIES = 3


# ══════════════════════════════════════════════════════════════════════════════
# پرامپتهای سیستمی
# ══════════════════════════════════════════════════════════════════════════════

def build_interview_system_prompt(
    knowledge_type: str,
    user_profile: dict | None = None,
) -> str:
    prof = user_profile or {}
    user_name = prof.get("full_name") or prof.get("reporter_name") or "همکار گرامی"
    user_position = prof.get("position") or prof.get("reporter_title") or "کارشناس/مهندس"
    user_project = prof.get("project_name") or prof.get("project") or "پروژه‌های مپنا توسعه یک"

    type_label = TYPE_LABELS.get(knowledge_type, knowledge_type)
    fields_info = FIELD_SCHEMAS.get(knowledge_type, {})
    fields_text = "\n".join(f'- "{k}": {desc}' for k, desc in fields_info.items())
    glossary_subset = _extract_relevant_glossary(user_project)

    button_lines = ""
    btn_map = BUTTON_FIELDS.get(knowledge_type, {})
    for k, options in btn_map.items():
        button_lines += f"\nکلید ویژهٔ «{k}»: یکی از {options} (نه متن آزاد)"

    dana_required: dict[str, str] = {
        "lesson": "شرح درس آموخته (ترکیب زمینه/مشکل/اقدام/درس) و نتیجه اجرا",
        "suggestion": "وضع موجود، پیشنهاد بهبود، تاثیر اجرای پیشنهاد (کیفی/کمی)، و نتایج حاصل از اجرای پیشنهاد",
        "explicit": "عنوان و شرح کامل منبع",
    }
    required_line = dana_required.get(knowledge_type, "فیلدهای کلیدی این نوع دانش")

    return f"""تو «دستیار مصاحبه‌گر مدیریت دانش شرکت مپنا توسعه یک (MAPNA MD1)» هستی.
طرف مصاحبه تو یکی از مهندسان مجرب شرکت در کارگاه یا سایت اجرایی است.

مشخصات همکار مصاحبه‌شونده:
- نام: {user_name}
- سمت: {user_position}
- پروژه/محل خدمت: {user_project}

نوع دانش در حال مصاحبه: {type_label}

واژه‌نامه و اصطلاحات فنی مجاز نیروگاهی/صنعتی:
{glossary_subset}

فیلدهای رسمی سامانه دانا که باید در این مصاحبه تکمیل شوند:
{fields_text}
{button_lines}

⚠️ فیلدهای الزامی فرم دانا که حتماً باید پوشش داده شوند:
{required_line}
(«بذر پیشنهاد» جزو سؤالات تو نیست — طبق فرم دانا خالی میماند.)

نکته مهم ورودی صوتی (STT):
متن کاربر حاصل تبدیل گفتار به نوشتار است و ممکن است غلط املائی جزئی داشته باشد (مثلاً «توربین گز» به‌جای «توربین گاز»). قبل از قضاوت، نیت را با اغماض حدس بزن و اصطلاح را به نزدیک‌ترین مورد واژه‌نامه بالا نگاشت کن؛ غلط تایپی را دلیل خالی گذاشتن فیلد یا تغییر طبقه‌بندی قرار نده و هرگز به خاطر یک حرف اشتباه، فیلد حیاتی را missing حساب نکن.

قوانین حیاتی مصاحبه:
۱. ادبیات تو صمیمانه، محترمانه، فنی و دقیقاً متناسب با فضای کارگاهی سایت است.
۲. در هر پیام فقط و فقط «یک سؤال شفاف و کوتاه» بپرس.
۳. قاعده تفکیک درس‌آموخته / پیشنهاد / دانش صریح (خیلی مهم — همیشه بررسی کن):
   - اگر نوع فعلی «درس‌آموخته» است ولی کاربر از یک پیشنهادِ اجراشده‌نشده حرف می‌زند (نشانه‌ها: «پیشنهادم اینه»، «باید ... بشه»، «اگر ... کنیم»، «نتیجه‌اش این میشه که ... خواهد شد») → switch_to_type را "suggestion" قرار بده.
   - اگر نوع فعلی «پیشنهاد» است ولی کاربر از اقدامِ انجام‌شده با نتیجهٔ واقعی حرف می‌زند (نشانه‌ها: «انجام دادیم»، «نتیجه شد»، «مشکل برطرف شد») → switch_to_type را "lesson" قرار بده.
   - اگر از منبع/کتاب/استاندارد/مقاله/گزارش حرف می‌زند → "explicit".
   فقط وقتی مطمئنی switch کن، وگرنه null بگذار.
۴. هرگز شماره تجهیز را به تجهیز دیگر نسبت نده.
۵. هر زمان فیلدهای حیاتی پر شدند، done را true کن.

خروجی JSON:
{{
  "extracted": {{"<field_key>": "<مقدار>"}},
  "switch_to_type": "<lesson|suggestion|explicit|null>",
  "ask": "<سؤال بعدی>",
  "done": false
}}
پایان: {{"done": true, "fields": {{...}}, "title": "...", "summary": "..."}}"""


def build_polish_system_prompt() -> str:
    """پرامپت سیستم برای پاس polish نهایی فرم DANA (خروجی فیلدبهفیلد)."""
    return """تو یک دستیار آمادهسازی فرم DANA هستی.
یک رکورد دانش سازمانی دریافت میکنی و باید آن را برای ثبت نهایی در
سامانه دانا آماده کنی.

وظایف:
1. محتوای فیلدهای رسمی فرم را جداگانه و به زبان فارسی حرفهای بازنویسی کن —
   هر فیلد خروجی مستقل دارد؛ هیچگاه چند فیلد را در یک متن ادغام نکن:
   - درسآموخته: «polished_description» = شرح درسآموخته (زمینه → مشکل →
     اقدام → نتیجه → درس اصلی، روان و ساختارمند).
   - پیشنهاد: «polished_current_state» = وضع موجود، و
     «polished_proposal» = پیشنهاد بهبود (دو متن کاملاً جدا).
   - دانش صریح: «polished_description» = شرح/توضیحات منبع.
2. اگر نام پروژه در شرح اولیه یا فیلدها ذکر شده، استخراج کن.
3. تا ۵ هشتگ مرتبط (فارسی، بدون #) پیشنهاد بده.
4. اگر عنوان فعلی ضعیف یا نامفهوم است، پیشنهاد بهتر بده.

خروجی JSON خالص (فقط کلیدهای مربوط به همین نوع دانش + کلیدهای مشترک):
{
  "polished_description": "<برای درسآموخته/دانش صریح یا null>",
  "polished_current_state": "<فقط پیشنهاد یا null>",
  "polished_proposal": "<فقط پیشنهاد یا null>",
  "extracted_project": "<نام پروژه یا null>",
  "hashtags": ["برچسب۱", "برچسب۲", ...],
  "title_suggestion": "<پیشنهاد عنوان بهتر یا null>"
}

قواعد: هیچ واقعیت، عدد یا نتیجهای که در ورودی نیست اضافه نکن.
اگر چیزی برای گفتن نداری، مقدار null بگذار."""


def build_tree_suggestion_system_prompt(knowledge_type: str) -> str:
    """پرامپت سیستم برای پیشنهاد مسیر درخت دانش."""
    type_label = TYPE_LABELS.get(knowledge_type, knowledge_type)
    tree_yaml = tree_as_yaml()
    return f"""تو یک دستیار طبقه‌بندی درخت دانش هستی.
یک دانش سازمانی دریافت میکنی و باید آن را در درخت رسمی سازمان قرار دهی.

نوع دانش: {type_label}

درخت رسمی دانش (فقط این نودها مجازند — اختراع نکن، تغییر نام نده):
{tree_yaml}

وظیفه: ۳ مسیر برتر (از ریشه تا برگ) پیشنهاد بده که بهترین تناسب را
با محتوای این دانش دارند. confidence بین۰ تا۱.

خروجی JSON خالص:
{{
  "suggestions": [
    {{
      "path": ["نود ریشه", "نود سطح۲", "نود سطح۳", "نود برگ"],
      "confidence": 0.87,
      "reason": "<یک جمله فارسی دلیل>"
    }}
  ]
}}"""


# ══════════════════════════════════════════════════════════════════════════════
# توابع LLM (با retry روی JSON نامعتبر)
# ══════════════════════════════════════════════════════════════════════════════

async def _call_llm_json(messages: list[dict]) -> dict:
    """
    فراخوانی LLM و پارس پاسخ به dict.
    در صورت خطا یا JSON نامعتبر، تا _MAX_JSON_RETRIES بار retry میکند؛
    در نهایت اگر همه شکست خوردند، یک dict حداقلی با ask ساخته‌شده از آخرین
    پاسخ خام مدل برمیگرداند (به‌جای {} خالی) تا کاربر «ادامه بدهید» بی‌هدف نبیند.
    """
    last_err: Exception | None = None
    last_content: str | None = None
    for attempt in range(_MAX_JSON_RETRIES + 1):
        try:
            content = await _call_llm_messages(messages, temperature=0.2, max_tokens=4096)
            last_content = content
            parsed = _parse_json_response(content)
            if isinstance(parsed, dict):
                return parsed
            logger.warning("پاسخ LLM یک dict نبود (attempt %d): %r", attempt + 1, type(parsed))
        except Exception as exc:
            last_err = exc
            logger.warning("خطا در فراخوانی LLM (attempt %d): %s", attempt + 1, exc)
    if last_err is not None:
        logger.error("شکست همهٔ تلاش‌ها: %s", last_err)
    # fallback: از متن خام مدل یک ask ساده بساز
    if last_content:
        text = (last_content or "").strip()
        # کوتاه و مرتب کن — هر چیز غیر از JSON
        text = text.replace("```", "").replace("json", "", 1).strip()
        if text and len(text) < 500:
            return {"ask": text}
    return {}


def _normalize_interview_response(parsed: dict) -> dict:
    """نرمالسازی پاسخ مصاحبه به شکل استاندارد داخلی."""
    result: dict[str, Any] = {
        "extracted": None,
        "ask": None,
        "done": False,
        "title": None,
        "summary": None,
        "fields": None,
        "error": None,
    }
    if not isinstance(parsed, dict):
        return result

    if parsed.get("done"):
        result["done"] = True
        f = parsed.get("fields")
        if isinstance(f, dict):
            result["fields"] = f
        t = parsed.get("title")
        if isinstance(t, str):
            result["title"] = t.strip() or None
        s = parsed.get("summary")
        if isinstance(s, str):
            result["summary"] = s.strip() or None
        return result

    ask = parsed.get("ask")
    if isinstance(ask, str) and ask.strip():
        result["ask"] = ask.strip()

    extracted = parsed.get("extracted")
    if isinstance(extracted, dict) and extracted:
        result["extracted"] = extracted

    return result


# ══════════════════════════════════════════════════════════════════════════════
# API اصلی: مصاحبه
# ══════════════════════════════════════════════════════════════════════════════

async def interview_next_turn(
    knowledge_type: str,
    history: list[dict],
    user_message: str,
    user_profile: dict | None = None,
) -> dict:
    """
    یک نوبت مکالمه با LLM — با پشتیبانی پروفایل مصاحبه‌شونده و تشخیص تغییر نوع دانش.

    ورودی:
        knowledge_type: 'lesson' | 'suggestion' | 'explicit'
        history: لیست قبلی پیامها
        user_message: آخرین پیام اپراتور
        user_profile: دیکشنری پروفایل (full_name/position/project_name) برای شخصی‌سازی پرامپت

    خروجی: مانند قبل + کلید "switch_to_type" برای پیشنهاد تغییر نوع دانش
    """
    if not is_ai_enabled():
        logger.info("AI غیرفعال — مصاحبه در حالت مکانیکی")
        return {
            "extracted": None, "ask": None, "done": False,
            "title": None, "summary": None, "fields": None,
            "switch_to_type": None,
            "error": "ai_disabled",
        }

    # نرمال‌سازی غیرتهاجمی پیام کاربر (شفاف)
    _orig_msg = user_message
    if _orig_msg:
        try:
            import config as _cfg
            if _cfg.ENABLE_TEXT_NORMALIZER:
                from utils.text_normalizer import normalize_for_llm as _norm2
                user_message = _norm2(user_message, threshold=_cfg.TEXT_NORMALIZER_THRESHOLD)
                if user_message != _orig_msg:
                    logger.debug("interview normalized: %r -> %r", _orig_msg[:300], user_message[:300])
        except Exception:
            logger.exception("text_normalizer interview failed — ادامه با متن اصلی")
            user_message = _orig_msg

    system = build_interview_system_prompt(knowledge_type, user_profile)
    messages: list[dict] = [{"role": "system", "content": system}]
    for entry in history:
        role = entry.get("role")
        content = entry.get("content")
        if role in ("user", "assistant") and isinstance(content, str):
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})

    parsed = await _call_llm_json(messages)
    if not parsed:
        return {
            "extracted": None,
            "ask": "پاسخ دریافت نشد؛ لطفاً دوباره بفرمایید یا پایان مصاحبه را بزنید.",
            "done": False,
            "title": None,
            "summary": None,
            "fields": None,
            "switch_to_type": None,
            "error": "llm_failed",
        }

    return {
        "extracted": parsed.get("extracted") if isinstance(parsed.get("extracted"), dict) else {},
        "switch_to_type": parsed.get("switch_to_type"),
        "ask": parsed.get("ask"),
        "done": bool(parsed.get("done")),
        "title": parsed.get("title"),
        "summary": parsed.get("summary"),
        "fields": parsed.get("fields") if isinstance(parsed.get("fields"), dict) else None,
        "error": None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# API: پاس polish نهایی
# ══════════════════════════════════════════════════════════════════════════════

async def polish_dana_draft(
    knowledge_type: str,
    fields: dict,
    raw_description: str | None,
    project_name: str | None = None,
) -> dict:
    """
    پاس polish — بازنویسی حرفهای فیلدبهفیلد + استخراج پروژه + هشتگ + عنوان.

    خروجی (کلیدهای هر نوع فقط در صورت موفقیت پر میشوند):
        {
            "polished_description": str | None,      # درسآموخته / دانش صریح
            "polished_current_state": str | None,    # پیشنهاد
            "polished_proposal": str | None,         # پیشنهاد
            "extracted_project": str | None,
            "hashtags": list[str] | None,
            "title_suggestion": str | None,
        }

    اگر AI در دسترس نباشد → همه None.
    """
    empty = {
        "polished_description": None,
        "polished_current_state": None,
        "polished_proposal": None,
        "extracted_project": project_name,
        "hashtags": None,
        "title_suggestion": None,
    }
    if not is_ai_enabled():
        return empty

    type_label = TYPE_LABELS.get(knowledge_type, knowledge_type)
    fields_json = json.dumps(fields, ensure_ascii=False, indent=2)
    system = build_polish_system_prompt()
    user = f"""نوع دانش: {type_label}

فیلدهای پرشده:
{fields_json}

شرح اولیه (ممکن است خالی باشد):
{raw_description or '(خالی)'}"""

    parsed = await _call_llm_json([
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])

    if not parsed:
        return empty

    result = dict(empty)

    def _set_str(target_key: str, *source_keys: str) -> None:
        for sk in source_keys:
            v = parsed.get(sk)
            if isinstance(v, str) and v.strip():
                result[target_key] = v.strip()
                return

    _set_str("polished_description", "polished_description")
    _set_str("polished_current_state", "polished_current_state")
    _set_str("polished_proposal", "polished_proposal")
    ep = parsed.get("extracted_project")
    if isinstance(ep, str) and ep.strip():
        result["extracted_project"] = ep.strip()
    ht = parsed.get("hashtags")
    if isinstance(ht, list):
        tags = [str(h).strip().lstrip("#") for h in ht if str(h).strip()]
        result["hashtags"] = tags[:5] or None
    ts = parsed.get("title_suggestion")
    if isinstance(ts, str) and ts.strip():
        result["title_suggestion"] = ts.strip()
    return result


# ══════════════════════════════════════════════════════════════════════════════
# API: QA سبک — غیرمسدودکننده
# ══════════════════════════════════════════════════════════════════════════════

def build_qa_system_prompt() -> str:
    """پرامپت QA سبک مطابق references/quality-assurance.md (نسخه گفتگومحور)."""
    return """تو یک بازبین کیفیت سبک رکوردهای دانش سازمانی هستی (مطابق موتور QA مهارت).
رکورد را از نظر «ادعاهای بیپشتوانه» بررسی کن؛ بازنویسی نکن.

چیزی که فلگ میکنی (حداکثر ۵ مورد):
- عدد/درصد/مبلغ/صرفهجویی که در ورودی کاربر وجود ندارد یا بزرگنمایی شده.
- علتِ قطعیِ اثباتنشده (علت حدسی بهجای «علت قطعی» باید علامت بخورد).
- ادغام «اثر مورد انتظار» با «نتیجه واقعی» (در پیشنهادها رایج است).
- تناقض داخلی آشکار بین فیلدها.

خروجی JSON خالص:
{
  "flags": [
    {"field": "<کلید فیلد یا عنوان کوتاه>", "issue": "<یک جمله فارسی>"}
  ]
}

اگر مشکلی نیست: {"flags": []}. هیچ‌وقت فیلدی را اختراع نکن."""


async def qa_flags(
    knowledge_type: str,
    fields: dict,
    raw_description: str | None,
) -> list[dict]:
    """
    QA سبک غیرمسدودکننده — فهرست هشدارها برای نمایش به اپراتور.
    خروجی: [{"field": str, "issue": str}] (حداکثر ۵) — خطا/AI خاموش → [].
    """
    if not is_ai_enabled():
        return []

    type_label = TYPE_LABELS.get(knowledge_type, knowledge_type)
    fields_json = json.dumps(fields, ensure_ascii=False, indent=2)
    try:
        parsed = await _call_llm_json([
            {"role": "system", "content": build_qa_system_prompt()},
            {"role": "user", "content": (
                f"نوع دانش: {type_label}\n\nفیلدها:\n{fields_json}\n\n"
                f"شرح اولیه:\n{raw_description or '(خالی)'}"
            )},
        ])
    except Exception:
        logger.exception("QA سبک ناموفق — بدون هشدار ادامه میدهیم")
        return []

    flags = parsed.get("flags") if isinstance(parsed, dict) else None
    if not isinstance(flags, list):
        return []
    out: list[dict] = []
    for f in flags:
        if not isinstance(f, dict):
            continue
        field = f.get("field")
        issue = f.get("issue")
        if isinstance(field, str) and isinstance(issue, str) and issue.strip():
            out.append({"field": field.strip()[:60], "issue": issue.strip()[:300]})
        if len(out) >= 5:
            break
    return out


# ══════════════════════════════════════════════════════════════════════════════
# API: پیشنهاد درخت دانش
# ══════════════════════════════════════════════════════════════════════════════

async def suggest_tree_paths(
    knowledge_type: str,
    fields: dict,
    raw_description: str | None,
    title: str | None = None,
    top_k: int = 3,
) -> list[dict]:
    """
    ۳ پیشنهاد برتر مسیر درخت دانش برای محتوای داده‌شده.

    خروجی: [{"{"path: [...], confidence: float, reason: str"}, ...}]
    فقط مسیرهایی که در درخت رسمی وجود دارند برگردانده میشوند.
    اگر AI در دسترس نباشد → [].
    """
    if not is_ai_enabled():
        return []

    type_label = TYPE_LABELS.get(knowledge_type, knowledge_type)
    fields_json = json.dumps(fields, ensure_ascii=False, indent=2)
    system = build_tree_suggestion_system_prompt(knowledge_type)
    user = f"""نوع: {type_label}
عنوان: {title or ''}
فیلدها: {fields_json}
شرح: {raw_description or ''}"""

    parsed = await _call_llm_json([
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])

    if not parsed:
        return []

    suggestions = parsed.get("suggestions")
    if not isinstance(suggestions, list):
        return []

    validated: list[dict] = []
    for s in suggestions:
        if not isinstance(s, dict):
            continue
        path = s.get("path")
        if not isinstance(path, list):
            continue
        path = [str(n) for n in path]
        if not validate_path(path):
            continue
        conf = s.get("confidence")
        if not isinstance(conf, (int, float)):
            conf = 0.5
        conf = max(0.0, min(1.0, float(conf)))
        reason = s.get("reason")
        if not isinstance(reason, str):
            reason = ""
        validated.append({"path": path, "confidence": conf, "reason": reason.strip()})
        if len(validated) >= top_k:
            break

    return validated