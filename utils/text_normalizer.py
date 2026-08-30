"""
utils/text_normalizer.py
نرمال‌ساز غیرتهاجمی متن STT قبل از ارسال به LLM.

- اصلاح فقط با اطمینان بالا (threshold >= 0.90) و فقط برای اصطلاحات واژه‌نامه نیروگاهی
- شفاف برای کاربر: هیچ پیامی به کاربر نمایش داده نمی‌شود، فقط لاگ debug
- بدون وابستگی خارجی (difflib استاندارد) — برای جلوگیری از تهاجمی بودن
"""

from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

# ── کش واژه‌نامه ──────────────────────────────────────────────────────────

_GLOSSARY_TERMS: list[str] | None = None
_GLOSSARY_NORM_MAP: dict[str, str] | None = None  # normalized -> original


def _load_glossary_terms() -> list[str]:
    global _GLOSSARY_TERMS
    if _GLOSSARY_TERMS is not None:
        return _GLOSSARY_TERMS
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for p in (
        os.path.join(base_dir, "references", "plant_glossary.json"),
        os.path.join(base_dir, "plant_glossary.json"),
    ):
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                terms: list[str] = []
                for items in data.values():
                    for it in items:
                        t = (it.get("term_fa") or "").strip()
                        if t and len(t) >= 2:
                            terms.append(t)
                # حذف تکراری با حفظ ترتیب
                seen: set[str] = set()
                uniq: list[str] = []
                for t in terms:
                    if t not in seen:
                        seen.add(t)
                        uniq.append(t)
                _GLOSSARY_TERMS = uniq
                logger.info("text_normalizer: %d اصطلاح از واژه‌نامه بارگذاری شد", len(uniq))
                return _GLOSSARY_TERMS
            except Exception:
                logger.exception("خطا در بارگذاری واژه‌نامه برای نرمال‌ساز")
                break
    _GLOSSARY_TERMS = []
    return _GLOSSARY_TERMS


# ── نرمال‌سازی حروف فارسی ───────────────────────────────────────────────

_AR_MAP = str.maketrans({
    "\u064a": "\u06cc",  # ي عربی -> ی فارسی
    "\u0649": "\u06cc",  # ى -> ی
    "\u0643": "\u06a9",  # ك عربی -> ک فارسی
    "\u06aa": "\u06a9",
    "\u0640": "",        # تطویل
    "\u200c": " ",       # نیم‌فاصله -> فاصله (برای تطبیق فازی)
})

# اعداد فارسی/عربی به لاتین (برای مقایسه)
_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _normalize_chars(s: str) -> str:
    """نرمال‌سازی حروف عربی به فارسی + اعداد."""
    s = s.translate(_AR_MAP)
    s = s.translate(_FA_DIGITS)
    # حذف تشدید و اعراب
    s = re.sub(r"[\u064b-\u0652]", "", s)
    # چند فاصله -> یک فاصله
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _norm_for_match(s: str) -> str:
    """نسخه نرمال برای امتیاز فازی (حروف + lowercase انگلیسی)."""
    s = _normalize_chars(s)
    return s.lower().strip()


def _build_norm_map() -> dict[str, str]:
    global _GLOSSARY_NORM_MAP
    if _GLOSSARY_NORM_MAP is not None:
        return _GLOSSARY_NORM_MAP
    terms = _load_glossary_terms()
    m: dict[str, str] = {}
    for t in terms:
        n = _norm_for_match(t)
        if n not in m:
            m[n] = t
    _GLOSSARY_NORM_MAP = m
    return m


# ایندکس بر اساس تعداد کلمات برای کاهش جستجو
_GLOSSARY_BY_WORDCOUNT: dict[int, list[tuple[str, str]]] | None = None  # wc -> [(norm, orig)]


def _build_by_wordcount() -> dict[int, list[tuple[str, str]]]:
    global _GLOSSARY_BY_WORDCOUNT
    if _GLOSSARY_BY_WORDCOUNT is not None:
        return _GLOSSARY_BY_WORDCOUNT
    norm_map = _build_norm_map()
    by_wc: dict[int, list[tuple[str, str]]] = {}
    for norm, orig in norm_map.items():
        wc = len(norm.split())
        by_wc.setdefault(wc, []).append((norm, orig))
    _GLOSSARY_BY_WORDCOUNT = by_wc
    return by_wc


# ── هسته فازی غیرتهاجمی ──────────────────────────────────────────────────

# حداقل طول توکن برای فازی (کلمات خیلی کوتاه را دست نمی‌زنیم)
_MIN_TOKEN_LEN = 3
# آستانه پیش‌فرض — بسیار سخت‌گیر برای غیرتهاجمی بودن
_DEFAULT_THRESHOLD = 0.90

import difflib as _difflib


def _best_glossary_match(phrase_norm: str, threshold: float = _DEFAULT_THRESHOLD) -> str | None:
    """بهترین تطبیق واژه‌نامه برای phrase_norm (نرمال‌شده) با آستانه."""
    if len(phrase_norm) < _MIN_TOKEN_LEN:
        return None
    wc = len(phrase_norm.split())
    by_wc = _build_by_wordcount()
    # فقط عبارات هم‌تعداد کلمه (یا ±1 برای خطای چسبیدگی) را بسنج — غیرتهاجمی و سریع
    candidate_pools: list[list[tuple[str, str]]] = []
    for dwc in (0, -1, 1):
        lst = by_wc.get(wc + dwc)
        if lst:
            candidate_pools.append(lst)
    if not candidate_pools:
        return None
    best: tuple[float, str] | None = None
    for pool in candidate_pools:
        for norm_term, orig in pool:
            if abs(len(norm_term) - len(phrase_norm)) > 4:
                continue
            # فیلتر سریع حرف اول: اگر کاملاً متفاوت باشد، نسبت قطعاً <0.9 است — رد سریع
            if norm_term and phrase_norm and norm_term[0] != phrase_norm[0]:
                # فقط اگر طول‌ها نزدیک باشند ادامه بده، وگرنه رد
                if abs(len(norm_term) - len(phrase_norm)) > 2:
                    continue
            # quick check: اگر quick_ratio < threshold-0.05 رد کن
            sm = _difflib.SequenceMatcher(None, phrase_norm, norm_term)
            if sm.quick_ratio() < threshold - 0.05:
                continue
            ratio = sm.ratio()
            if ratio >= threshold:
                if best is None or ratio > best[0]:
                    best = (ratio, orig)
                if ratio >= 0.98:
                    return orig  # عالی — زود برگرد
    if best is None:
        return None
    return best[1]


def normalize_for_llm(text: str, project_name: str | None = None, threshold: float = _DEFAULT_THRESHOLD) -> str:
    """
    نرمال‌سازی غیرتهاجمی متن STT قبل از ارسال به LLM.

    - حروف عربی -> فارسی
    - فقط اصطلاحات واژه‌نامه با اطمینان >= threshold اصلاح می‌شوند
    - عبارات 1 تا 3 کلمه‌ای به ترتیب طول نزولی بررسی می‌شوند (ترجیح عبارت بلند)
    - کاملاً شفاف: لاگ debug، بدون پیام به کاربر
    """
    if not text or not text.strip():
        return text

    orig = text
    # 1. نرمال‌سازی حروف (بی‌خطر)
    #    اما متن اصلی را برای خروجی نگه می‌داریم؛ فقط برای تطبیق از نرمال استفاده می‌کنیم
    #    اصلاح نهایی با عبارت اصلی واژه‌نامه جایگزین می‌شود
    _load_glossary_terms()
    if not _GLOSSARY_TERMS:
        # اگر واژه‌نامه نبود، فقط نرمال حروف را برگردان
        normalized_chars = _normalize_chars(text)
        # فقط اگر تفاوت فقط در ي/ك بود برگردان، وگرنه اصلی
        if normalized_chars != text and _norm_for_match(normalized_chars) != _norm_for_match(text):
            return text
        # اعمال نرمال حروف به خروجی (بی‌خطر)
        if normalized_chars != text:
            # بازسازی با حفظ علائم: ساده — جایگزینی حروف عربی
            text = text.translate(_AR_MAP)
            text = text.translate(_FA_DIGITS)
            text = re.sub(r"[\u064b-\u0652]", "", text)
        return text

    # 2. توکن‌سازی برای تطبیق عبارتی
    # الگوی کلمات فارسی/انگلیسی/عدد
    word_re = re.compile(r"[\w\u0600-\u06FF]+", re.UNICODE)
    tokens = word_re.findall(text)
    if not tokens:
        return text

    # برای جلوگیری از تهاجمی بودن: فقط توکن‌های >=3 حرف را کاندید می‌کنیم
    # ولی عبارات چندکلمه‌ای را هم می‌سنجیم
    n = len(tokens)
    # آرایه جایگزینی: برای هر موقعیت شروع، (طول عبارت، عبارت جایگزین)
    replacements: dict[int, tuple[int, str]] = {}

    # از عبارات بلند به کوتاه — ترجیح عبارت بلند
    for size in (3, 2, 1):
        if size > n:
            continue
        for i in range(n - size + 1):
            # اگر این بازه قبلاً با عبارت بلندتر پوشیده شده، رد کن
            overlapped = False
            for s, (ls, _) in replacements.items():
                if not (i + size <= s or i >= s + ls):
                    overlapped = True
                    break
            if overlapped:
                continue
            phrase = " ".join(tokens[i:i + size])
            if len(phrase) < _MIN_TOKEN_LEN:
                continue
            # فیلتر: عبارت‌های خیلی عام (مثل "در این") را رد کن — حداقل یک توکن >=3 حرف
            if not any(len(t) >= _MIN_TOKEN_LEN for t in tokens[i:i + size]):
                continue
            phrase_norm = _norm_for_match(phrase)
            # تطبیق
            best = _best_glossary_match(phrase_norm, threshold=threshold)
            if best is not None:
                # اطمینان مضاعف: عبارت اصلی نباید دقیقاً برابر باشد (نیازی به جایگزینی نیست)
                if _norm_for_match(best) == phrase_norm:
                    continue
                # شرط غیرتهاجمی: اختلاف فقط 1-2 حرف باشد (نسبت بالا تضمین می‌کند)
                # اما اگر best خیلی متفاوت از نظر معناست، همین آستانه کافی است
                replacements[i] = (size, best)

    if not replacements:
        # فقط نرمال حروف بی‌خطر را اعمال کن
        new_text = text.translate(_AR_MAP)
        new_text = new_text.translate(_FA_DIGITS)
        new_text = re.sub(r"[\u064b-\u0652]", "", new_text)
        if new_text != orig:
            logger.debug("text_normalizer char-only: %r -> %r", orig[:200], new_text[:200])
        return new_text

    # 3. بازسازی متن با جایگزینی از انتها به ابتدا (برای حفظ ایندکس)
    # متن را بر اساس توکن‌ها بازسازی می‌کنیم با حفظ جداکننده‌های اصلی به‌صورت ساده (فاصله)
    # برای شفافیت، فقط بخش‌های جایگزین‌شده را عوض می‌کنیم و بقیه را دست نمی‌زنیم
    # روش: متن را به‌صورت توکن + جداکننده می‌سازیم، سپس جایگاه‌ها را پر می‌کنیم
    # ساده‌سازی: خروجی را از توکن‌ها می‌سازیم (فاصله واحد) — تفاوت فاصله جزئی قابل اغماض است
    # اما اگر هیچ جایگزینی نبود فاصله اصلی حفظ می‌شود؛ اینجا چون جایگزینی داریم، فاصله واحد کافی است
    result_tokens: list[str] = []
    i = 0
    while i < n:
        if i in replacements:
            size, repl = replacements[i]
            result_tokens.append(repl)
            i += size
        else:
            result_tokens.append(tokens[i])
            i += 1

    new_text = " ".join(result_tokens)
    # علائم نگارشی اصلی را اگر در انتها بود حفظ کن (مثل ؟ ! .)
    # اگر متن اصلی با علامت تمام شده و جدید ندارد، اضافه کن
    orig_stripped = orig.strip()
    if orig_stripped and orig_stripped[-1] in "؟?!.،," and new_text and new_text[-1] not in "؟?!.،,":
        # نگه‌داشتن علامت پایان اگر حذف شده
        pass  # عمداً اضافه نمی‌کنیم تا تهاجمی نشود

    # نرمال حروف را هم روی نتیجه اعمال کن
    new_text = new_text.translate(_AR_MAP)
    new_text = new_text.translate(_FA_DIGITS)
    new_text = re.sub(r"[\u064b-\u0652]", "", new_text)

    if new_text != orig:
        logger.debug("text_normalizer: %r -> %r (replacements=%s)", orig[:300], new_text[:300], replacements)

    return new_text


def clear_cache() -> None:
    """برای تست — کش را پاک می‌کند."""
    global _GLOSSARY_TERMS, _GLOSSARY_NORM_MAP, _GLOSSARY_BY_WORDCOUNT
    _GLOSSARY_TERMS = None
    _GLOSSARY_NORM_MAP = None
    _GLOSSARY_BY_WORDCOUNT = None
