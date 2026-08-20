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
        "seed", "committee", "colleagues",
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

def build_interview_system_prompt(knowledge_type: str) -> str:
    """
    پرامپت سیستم برای مصاحبهٔ چندمرحلهای.
    """
    type_label = TYPE_LABELS.get(knowledge_type, knowledge_type)
    fields_list = INTERVIEW_FRAMEWORKS.get(knowledge_type, [])
    fields_lines = "\n".join(
        f'- "{k}": {FIELD_SCHEMAS.get(knowledge_type, {}).get(k, k)}'
        for k in fields_list if k in FIELD_SCHEMAS.get(knowledge_type, {})
    )
    button_lines = ""
    btn_map = BUTTON_FIELDS.get(knowledge_type, {})
    for k, options in btn_map.items():
        button_lines += (
            f"\nکلید ویژهٔ «{k}»: یکی از {options} (نه متن آزاد)"
        )

    return f"""تو یک مصاحبه‌گر دانش سازمانی هستی. یک اپراتور باتجربه در یک سایت
صنعتی (نیروگاه، پالایشگاه، کارخانه) پیش روی توست.
وظیفهٔ تو: کمک به او برای ثبت تجربه/دانشش مطابق ساختار استاندارد DANA.

نوع دانش: {type_label}

فیلدهایی که باید پر شوند:
{fields_lines}
{button_lines}

رفتار:
1. در هر نوبت فقط یک سؤال کوتاه و دقیق بپرس.
2. سؤال‌ها باید به زبان فارسی و متناسب با زبان صنعتی باشند.
3. اگر پاسخ کاربر اطلاعات چند فیلد را پوشش داد، همه را در extracted بنویس.
5. اگر چیزی مبهم مانده، سؤال روشن‌کننده بپرس (نه سؤال جدید).
6. زمانی done: true برگردان که حداقل همهٔ فیلدهای متنی پر شده باشند.

خروجی: فقط یک شیوهٔ JSON خالص (بدون backtick، بدون توضیح اضافه).
هر نوبت یکی از سه شکل:

1) پاسخ کاربر اطلاعاتی داد:
{{
  "extracted": {{"<key>": "<value>", ...}},
  "ask": "<سؤال بعدی یا خاتمه>"
}}

2) سؤال روشن‌کننده:
{{"ask": "<سؤال توضیحی>"}}

3) پایان مصاحبه:
{{
  "done": true,
  "fields": {{<همهٔ فیلدهای پرشده>}},
  "title": "<پیشنهاد عنوان کوتاه>",
  "summary": "<یک‌خط خلاصه برای تأیید کاربر>"
}}

قواعد:
- فقط از کلیدهای مجاز در فیلدها استفاده کن.
- مقادیر فارسی، طبیعی، خلاصه (۲ تا ۴ جمله).
- هیچ‌وقت فیلدی را حدس نزن."""


def build_polish_system_prompt() -> str:
    """پرامپت سیستم برای پاس polish نهایی فرم DANA."""
    return """تو یک دستیار آماده‌سازی فرم DANA هستی.
یک رکورد دانش سازمانی دریافت میکنی و باید آن را برای ثبت نهایی آماده کنی.

وظایف:
1. یک narrative حرفه‌ای به زبان فارسی بنویس که فیلدهای محتوایی را به شکل
   روان و ساختارمند ترکیب کند (مثلاً برای درس‌آموخته: زمینه → مشکل →
   اقدام → نتیجه → درس اصلی → توصیه).
2. اگر نام پروژه یا پیمانکار در شرح اولیه یا فیلدها ذکر شده، استخراج کن.
4. تا ۵ هشتگ مرتبط (فارسی، بدون #) پیشنهاد بده.
5. اگر عنوان فعلی ضعیف یا نامفهوم است، پیشنهاد بهتر بده.

خروجی JSON خالص:
{
  "narrative": "<متن narrative فارسی، ۳–۶ جمله>",
  "extracted_project": "<نام پروژه یا null>",
  "extracted_contractor": "<نام پیمانکار یا null>",
  "hashtags": ["برچسب۱", "برچسب۲", ...],
  "title_suggestion": "<پیشنهاد عنوان بهتر یا null>"
}

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
) -> dict:
    """
    یک نوبت مکالمه با LLM.

    ورودی:
        knowledge_type: 'lesson' | 'suggestion' | 'explicit'
        history: لیست قبلی پیامها به شکل [{"{"role: 'assistant'|'user', content: str"}, ...}]
        user_message: آخرین پیام اپراتور

    خروجی:
        {
            "extracted": dict[key, value] | None  — فیلدهای استخراج‌شده از این پاسخ
            "ask": str | None                       — سؤال بعدی برای کاربر
            "done": bool                            — آیا مصاحبه تمام شد؟
            "title": str | None                     — پیشنهاد عنوان (اگر done)
            "summary": str | None                   — خلاصه برای تأیید (اگر done)
            "fields": dict | None                   — همهٔ فیلدها (اگر done)
            "error": str | None                     — نوع خطا در صورت شکست
        }

    اگر AI در دسترس نباشد:
        خروجی = {extracted: None, ask: None, done: False, error: "ai_disabled"}
    """
    if not is_ai_enabled():
        logger.info("AI غیرفعال — مصاحبه در حالت مکانیکی")
        return {
            "extracted": None, "ask": None, "done": False,
            "title": None, "summary": None, "fields": None,
            "error": "ai_disabled",
        }

    system = build_interview_system_prompt(knowledge_type)
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
            "extracted": None, "ask": None, "done": False,
            "title": None, "summary": None, "fields": None,
            "error": "llm_failed",
        }

    result = _normalize_interview_response(parsed)

    # اگر AI سؤالی نساخت (پاسخ خالی/ناقص)، از فیلدهای پرشده‌نشده سؤال هدفمند بساز
    if not result.get("ask"):
        # فیلدهای پر شده تا الان (از تاریخچه استخراج نشده‌اند — از پارس پاسخها)
        # بهتر: از روی فیلدهای ناقص فریمورک، سؤال بعدی را پیشنهاد بده
        filled = set()
        for entry in history:
            content = entry.get("content") or ""
            if entry.get("role") == "assistant" and "extracted" in content:
                continue
        # از آخرین پاسخ استخراج‌شده استفاده کن
        last_extracted = result.get("extracted") or {}
        for k in last_extracted:
            filled.add(k)
        # فیلدهای باقی‌مانده از فریمورک
        fields_framework = INTERVIEW_FRAMEWORKS.get(knowledge_type, [])
        remaining = [k for k in fields_framework if k not in filled]
        if remaining:
            next_key = remaining[0]
            label = FIELD_SCHEMAS.get(knowledge_type, {}).get(next_key, next_key)
            result["ask"] = f"لطفاً «{label}» را توضیح دهید:"
        else:
            result["ask"] = "به نظر می‌رسد همهٔ فیلدهای کلیدی پر شده‌اند. اگر مورد دیگری هست بگویید، یا «✓ پایان مصاحبه» را بزنید."
    return result


# ══════════════════════════════════════════════════════════════════════════════
# API: پاس polish نهایی
# ══════════════════════════════════════════════════════════════════════════════

async def polish_dana_draft(
    knowledge_type: str,
    fields: dict,
    raw_description: str | None,
    project_name: str | None = None,
    contractor_name: str | None = None,
) -> dict:
    """
    پاس polish — narrative حرفه‌ای + استخراج پروژه/پیمانکار + هشتگ + پیشنهاد عنوان.

    خروجی:
        {
            "narrative": str | None,
            "extracted_project": str | None,
            "extracted_contractor": str | None,
            "hashtags": list[str] | None,
            "title_suggestion": str | None,
        }

    اگر AI در دسترس نباشد → همه None.
    """
    empty = {
        "narrative": None,
        "extracted_project": project_name,
        "extracted_contractor": contractor_name,
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
    narrative = parsed.get("narrative")
    if isinstance(narrative, str) and narrative.strip():
        result["narrative"] = narrative.strip()
    ep = parsed.get("extracted_project")
    if isinstance(ep, str) and ep.strip():
        result["extracted_project"] = ep.strip()
    ec = parsed.get("extracted_contractor")
    if isinstance(ec, str) and ec.strip():
        result["extracted_contractor"] = ec.strip()
    ht = parsed.get("hashtags")
    if isinstance(ht, list):
        tags = [str(h).strip().lstrip("#") for h in ht if str(h).strip()]
        result["hashtags"] = tags[:5] or None
    ts = parsed.get("title_suggestion")
    if isinstance(ts, str) and ts.strip():
        result["title_suggestion"] = ts.strip()
    return result


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