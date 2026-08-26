"""
engine/knowledge_ai.py
استخراج فیلدهای ساختاریافته از متن آزاد تجربه/دانش با LLM
(کلاینت سازگار با OpenAI — پروتکل /v1/chat/completions).

پیش‌فرض‌ها از config خوانده می‌شوند:
    KNOWLEDGE_AI_BASE_URL → https://opencode.ai/zen/go/v1 (OpenCode Go)
    KNOWLEDGE_AI_API_KEY  → KNOWLEDGE_AI_API_KEY یا OPENCODE_API_KEY
    KNOWLEDGE_AI_MODEL    → خالی = AI غیرفعال

اگر کلید یا مدل تنظیم نشده باشد، is_ai_enabled() == False و handler باید به
حالت «پرسش دستی همه فیلدها» برگردد (fallback امن — ربات بدون AI هم کار می‌کند).

وابستگی‌ها: config، httpx (وابستگی ترنسیتیو python-telegram-bot).
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx

import config

logger = logging.getLogger(__name__)

# ─── طرح فیلدها به ازای هر نوع دانش ──────────────────────────────────────────
# کلید = نام فیلد داخلی؛ مقدار = برچسب فارسی که به کاربر/در پیش‌نویس نشان داده می‌شود.
#
# توجه: فیلدهای impact_type (پیشنهاد) و subtype (دانش صریح) دکمه‌ای هستند و
# در FIELD_SCHEMAS قرار نمیگیرند — در extract_fields بهصورت جداگانه از JSON پاسخ
# LLM استخراج میشوند (impact_type) یا در handler با دکمه پرسیده میشوند (subtype).
# در مصاحبه نیز این فیلدها بهصورت اختصاصی (نه متن آزاد) پرسیده میشوند.
FIELD_SCHEMAS: dict[str, dict[str, str]] = {
    "lesson": {
        "context": "زمینه و بستر",
        "status": "وضعیت",
        "problem": "مشکل یا فرصت",
        "cause": "علت یا عوامل مؤثر",
        "action": "اقدام انجام‌شده",
        "result": "نتیجهٔ واقعی",
        "lesson": "درس اصلی آموخته‌شده",
        "transferability": "قابلیت انتقال",
        "recommendation": "توصیه برای دیگران",
    },
    "suggestion": {
        "current_state": "وضع موجود",
        "problem": "مشکل یا فرصت بهبود",
        "proposal": "پیشنهاد بهبود",
        "expected_impact": "نتایج حاصل از اجرای پیشنهاد (اثر مورد انتظار)",
        "colleagues": "همکاران درگیر",
    },
    # توجه: «بذر پیشنهاد» طبق فرم رسمی DANA باید خالی بماند و پرسیده نمیشود؛
    # «کمیته تخصصی» در مرحلهٔ اطلاعات سازمانی (نه فیلدهای متنی) پرسیده میشود.
    "explicit": {
        "subject": "موضوع اصلی",
        "description": "شرح کامل",
        "scope": "محدودهٔ سازمانی",
        "colleagues": "همکاران درگیر",
    },
}

# فیلدهایی که در مصاحبه بهصورت دکمهای پرسیده میشوند (نه متن آزاد)
BUTTON_FIELDS: dict[str, dict[str, list[str]]] = {
    "suggestion": {
        "impact_type": ["کیفی", "کمی"],
    },
    "explicit": {
        "subtype": [
            "کتاب", "محتوای آموزشی", "مقاله", "لینک", "گزارش بین‌المللی",
            "پادکست", "اختراع", "مجله", "استاندارد",
        ],
    },
}

TYPE_LABELS: dict[str, str] = {
    "lesson": "درس‌آموخته",
    "suggestion": "پیشنهاد",
    "explicit": "دانش صریح",
}

# ── اولویت فیلدها بر اساس ستاره‌های فرم رسمی DANA ────────────────────────────
# حیاتی = در فرم DANA ستاره‌دار است و باید اول پرسیده/تأمین شود؛
# مهم = ارزش تکمیل بالا ولی ستاره‌دار نیست؛ اختیاری = در انتها و قابل رد.
FIELD_PRIORITY: dict[str, dict[str, list[str]]] = {
    "lesson": {
        "critical": ["problem", "action", "result", "lesson"],
        "important": ["context", "cause", "recommendation"],
        "optional": ["status", "transferability"],
    },
    "suggestion": {
        # «نتایج حاصل از اجرای پیشنهاد» در DANA ستاره‌دار است و از
        # expected_impact + نشانگر «اثر مورد انتظار — تأیید نشده» ساخته میشود.
        "critical": ["current_state", "proposal", "expected_impact"],
        "important": ["problem"],
        "optional": ["colleagues"],
    },
    "explicit": {
        "critical": ["subject", "description"],
        "important": [],
        "optional": ["scope", "colleagues"],
    },
}


def order_fields_by_priority(knowledge_type: str, keys: list[str]) -> list[str]:
    """کلیدها را بر اساس اولویت فرم DANA مرتب میکند (حیاتی → مهم → اختیاری)."""
    prio = FIELD_PRIORITY.get(knowledge_type, {})
    rank: dict[str, int] = {}
    for tier, offset in (("critical", 0), ("important", 1000), ("optional", 2000)):
        for i, k in enumerate(prio.get(tier, [])):
            rank[k] = offset + i
    max_rank = 3000
    return sorted(keys, key=lambda k: rank.get(k, max_rank))

_MAX_DESCRIPTION_LEN = 4000


def is_ai_enabled() -> bool:
    """آیا استخراج با AI قابل استفاده است؟ (کلید + مدل باید تنظیم شده باشند)"""
    return bool(config.KNOWLEDGE_AI_API_KEY and config.KNOWLEDGE_AI_MODEL)


def field_labels(knowledge_type: str) -> dict[str, str]:
    """برچسب‌های فارسی فیلدهای یک نوع دانش."""
    return dict(FIELD_SCHEMAS.get(knowledge_type, {}))


def field_order(knowledge_type: str) -> list[str]:
    """ترتیب پرسش فیلدهای یک نوع دانش."""
    return list(FIELD_SCHEMAS.get(knowledge_type, {}))


def build_system_prompt(knowledge_type: str) -> str:
    """پرامپت سیستم برای استخراج فیلدهای نوع دانش مشخص."""
    labels = FIELD_SCHEMAS.get(knowledge_type, {})
    type_label = TYPE_LABELS.get(knowledge_type, knowledge_type)
    field_list = "\n".join(f'- "{key}": {label}' for key, label in labels.items())
    impact_line = (
        'و "impact_type": "کیفی" یا "کمی" '
        '(فقط برای نوع پیشنهاد؛ بر اساس اثر غالب: اگر اعداد/درصد/مبلغ دارد «کمی»، '
        'وگرنه «کیفی» — فقط یکی، بدون جمله)'
        if knowledge_type == "suggestion"
        else ""
    )
    return (
        "تو یک دستیار استخراج دانش سازمانی هستی. متن فارسی «تجربه یا دانش ثبتشده "
        "توسط یک اپراتور در سایت» را میگیری و آن را به فیلدهای ساختاریافته تبدیل میکنی.\n"
        f"\nنوع دانش: {type_label}\n"
        "\nفقط یک شیء JSON خالص برگردان (بدون backtick، بدون توضیح اضافه) به این شکل:\n"
        '{"title": "عنوان کوتاه و گویا (حداکثر ۱۲ کلمه)", '
        '"fields": {"<key>": "مقدار"}, '
        '"hashtags": ["برچسب۱", "برچسب۲"]'
        + (", \"impact_type\": \"کیفی\"" if knowledge_type == "suggestion" else "")
        + ', "classification": {"recommended": "...", "reason": "..."}}\n'
        "\nقواعد:\n"
        "- برای هر کلید از فهرست مجاز زیر، فقط اگر مقدارش بهصورت صریح یا با استنتاجِ "
        "مطمئن در متن موجود است آن را در fields بنویس؛ وگرنه آن کلید را نیاور.\n"
        "- هیچ فیلدی را حدس نزن یا اختراع نکن.\n"
        "- هر مقدار فارسی، طبیعی و خلاصه (۲ تا ۴ جمله) باشد.\n"
        '- "title" را از روی محتوای متن بساز (فارسی، کوتاه).\n'
        '- "hashtags": حداکثر ۵ برچسب مرتبط (فارسی، بدون #).\n'
        "\nطبقهبندی (در کلید classification) — بر اساس محتوای واقعی متن، نه نوع انتخابشده:\n"
        '- اگر متن یک تجربهٔ واقعی را توصیف میکند (اقدام انجامشده + نتیجه) → "lesson"\n'
        '- اگر متن یک پیشنهاد/توصیه برای آینده است (پیادهسازینشده) → "suggestion"\n'
        '- اگر متن یک منبع دانش موجود است (کتاب/مقاله/لینک/استاندارد/گزارش/...) → "explicit"\n'
        '- اگر قابل تشخیص نیست → "ambiguous"\n'
        '"recommended" یکی از این مقادیر، "reason" دلیل کوتاه فارسی.\n'
        f"{impact_line}\n"
        f"\nکلیدها و برچسبهای مجاز فیلدها برای این نوع:\n{field_list}"
    )


def _parse_json_response(content: str) -> dict:
    """JSON خام را از پاسخ مدل استخراج می‌کند (مقاوم به code fence و متن اضافه)."""
    text = content.strip()
    # حذف code fence (```json ... ```)
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    # برش بین اولین { و آخرین }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # تلاش برای ترمیم JSON ناقص (مدل وسط JSON قطع شده)
        repaired = _repair_truncated_json(text)
        if repaired is not None:
            return repaired
        # اگر ترمیم نشد، خطای اصلی را بده تا caller با fallback برخورد کند
        raise


def _repair_truncated_json(text: str) -> dict | None:
    """ترمیم JSON ناقص: رشتهٔ باز را می‌بندد یا کلید-مقدار بریده را حذف می‌کند."""
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None

    # اسکن سبک: عمق {} و وضعیت رشته و آخرین کامای سطح ۱ را ردگیری کن
    depth = 0
    in_string = False
    escape = False
    cut = -1  # آخرین نقطهٔ امن برای برش (کامای سطح ۱)
    for i, ch in enumerate(stripped):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "," and depth == 1:
            cut = i

    if in_string:
        # آخرین رشته باز مانده — آخرین کلید-مقدار کامل را نگه دار و بقیه را دور بریز
        if cut != -1:
            candidate = stripped[:cut].rstrip()
            if candidate.endswith(","):
                candidate = candidate[:-1].rstrip()
            candidate += "}"
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
        # اگر cut نبود (اولین/فقط کلید هم وسطش بریده)، رشته را ببند و } اضافه کن
        candidate = stripped + '"}'
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        return None

    if depth > 0:
        # چند سطح } کم دارد — اضافه کن
        candidate = stripped + "}" * depth
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    return None


def _fallback_title(knowledge_type: str, raw_text: str, fields: dict) -> str:
    """اگر مدل title ندهد، از اولین فیلد موجود عنوان می‌سازد."""
    for key in ("problem", "subject", "description", "current_state", "context"):
        value = fields.get(key)
        if value:
            return str(value).strip()[:80]
    return raw_text.strip()[:80]


async def extract_fields(knowledge_type: str, raw_text: str) -> dict:
    """
    فیلدهای ساختاریافته را از متن آزاد استخراج می‌کند.

    خروجی:
        {
            "title":     str | None — عنوان پیشنهادی
            "fields":    dict[key, str] — فقط فیلدهای استخراج‌شده
            "hashtags":  list[str] | None
            "impact_type": "کیفی"|"کمی"|None — فقط برای پیشنهاد
            "classification": {"recommended": str, "reason": str, "conflict": bool}
            "missing":   list[key] — فیلدهایی که در متن نبودند (نیاز به پرسش)
        }

    اگر AI غیرفعال باشد یا خطا بدهد، fields خالی و missing = همه فیلدهاست
    (handler همه را دستی می‌پرسد). هیچ استثنایی بیرون نمی‌رود.
    """
    empty_missing = order_fields_by_priority(knowledge_type, field_order(knowledge_type))
    # «تاثیر اجرای پیشنهاد» برای پیشنهادها همیشه باید پرسیده شود
    if knowledge_type == "suggestion":
        empty_missing.insert(0, "impact_type")
    empty = {
        "title": None,
        "fields": {},
        "hashtags": None,
        "impact_type": None,
        "classification": {"recommended": "ambiguous", "reason": "", "conflict": False},
        "missing": empty_missing,
    }
    if not is_ai_enabled():
        logger.info("AI غیرفعال است (کلید یا مدل تنظیم نشده) — همه فیلدها دستی پرسیده می‌شوند.")
        return empty

    system = build_system_prompt(knowledge_type)
    user_text = (raw_text or "").strip()[:_MAX_DESCRIPTION_LEN]
    if not user_text:
        return empty

    try:
        content = await _call_llm(system, user_text)
        parsed = _parse_json_response(content)
    except Exception:
        logger.exception("خطا در استخراج فیلدها با AI — برگشت به حالت دستی")
        return empty

    allowed = set(FIELD_SCHEMAS.get(knowledge_type, {}))
    raw_fields = parsed.get("fields")
    fields: dict[str, str] = {}
    if isinstance(raw_fields, dict):
        for key, value in raw_fields.items():
            if key in allowed and isinstance(value, str) and value.strip():
                fields[key] = value.strip()

    title = parsed.get("title")
    if not isinstance(title, str) or not title.strip():
        title = _fallback_title(knowledge_type, user_text, fields)

    hashtags = parsed.get("hashtags")
    if isinstance(hashtags, list):
        hashtags = [str(h).strip().lstrip("#") for h in hashtags if str(h).strip()]
        hashtags = hashtags[:5]
    else:
        hashtags = None

    # نوع اثر (فقط پیشنهاد): کیفی/کمی
    impact_type: str | None = None
    if knowledge_type == "suggestion":
        _it = parsed.get("impact_type")
        if isinstance(_it, str) and _it.strip() in ("کیفی", "کمی"):
            impact_type = _it.strip()

    # پیشنهاد طبقه‌بندی بر اساس محتوای واقعی (مطابق knowledge-classification.md)
    recommended = "ambiguous"
    reason = ""
    _cls = parsed.get("classification")
    if isinstance(_cls, dict):
        _rec = _cls.get("recommended")
        if isinstance(_rec, str) and _rec in ("lesson", "suggestion", "explicit", "ambiguous"):
            recommended = _rec
        if isinstance(_cls.get("reason"), str):
            reason = _cls["reason"].strip()
    conflict = recommended != knowledge_type

    missing = order_fields_by_priority(
        knowledge_type, [k for k in field_order(knowledge_type) if k not in fields]
    )
    # «تاثیر اجرای پیشنهاد» اگر از متن درنیامد، باید دستی پرسیده شود
    if knowledge_type == "suggestion" and impact_type is None:
        missing.insert(0, "impact_type")

    logger.info(
        "استخراج AI: %d فیلد از %d، %d فیلد ناقص، طبقه‌بندی=%s",
        len(fields), len(allowed), len(missing), recommended,
    )
    return {
        "title": title,
        "fields": fields,
        "hashtags": hashtags,
        "impact_type": impact_type,
        "classification": {"recommended": recommended, "reason": reason, "conflict": conflict},
        "missing": missing,
    }


async def _call_llm_messages(
    messages: list[dict],
    *,
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> str:
    """
    فراخوانی chat completions با زنجیرهٔ پروایدرها.

    ترتیب: پروایدر اصلی (سه متغیر config) سپس KNOWLEDGE_AI_PROVIDERS.
    هر پروایدر تا چند تلاش (۴ اگر تنها، ۲ اگر fallback دارد) امتحان می‌شود؛
    خطاهای 4xx (غیر از 429) قطعی‌اند و فوری رد می‌شوند. اگر همه شکست بخورند
    آخرین استثنا بالا پرتاب می‌شود.
    """
    providers: list[dict] = []
    if config.KNOWLEDGE_AI_API_KEY and config.KNOWLEDGE_AI_MODEL:
        providers.append({
            "base_url": config.KNOWLEDGE_AI_BASE_URL,
            "api_key": config.KNOWLEDGE_AI_API_KEY,
            "model": config.KNOWLEDGE_AI_MODEL,
        })
    providers.extend(config.KNOWLEDGE_AI_PROVIDERS)

    if not providers:
        raise RuntimeError("هیچ پروایدر AI پیکربندی نشده است")

    attempts_per_provider = 4 if len(providers) == 1 else 2

    last_exc: Exception | None = None
    for idx, prov in enumerate(providers, start=1):
        url = f"{prov['base_url'].rstrip('/')}/chat/completions"
        payload = {
            "model": prov["model"],
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {prov['api_key']}"}

        for attempt in range(1, attempts_per_provider + 1):
            try:
                async with httpx.AsyncClient(timeout=config.KNOWLEDGE_AI_TIMEOUT) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                try:
                    return data["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError) as exc:
                    raise RuntimeError(f"پاسخ LLM فرمت غیرمنتظره‌ای دارد: {exc}") from exc
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if 400 <= status < 500 and status != 429:
                    logger.warning(
                        "LLM provider %d مدل=%s → %s (خطای قطعی، پروایدر بعدی)",
                        idx, prov["model"], status,
                    )
                    last_exc = exc
                    break
                last_exc = exc
                logger.warning(
                    "LLM provider %d مدل=%s وضعیت=%s (attempt %d/%d)",
                    idx, prov["model"], status, attempt, attempts_per_provider,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                logger.warning(
                    "LLM provider %d شبکه/timeout (attempt %d/%d): %s",
                    idx, attempt, attempts_per_provider, exc,
                )

            await asyncio.sleep(2 * attempt)

        if idx < len(providers):
            logger.warning("پروایدر %d شکست خورد — رفتن به پروایدر بعدی", idx)

    raise last_exc if last_exc else RuntimeError("همهٔ پروایدرهای AI ناموفق بودند")


async def _call_llm(system: str, user_text: str) -> str:
    """یک فراخوانی chat completions تک‌پیام — wrapper برای backward compatibility."""
    return await _call_llm_messages(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ]
    )
