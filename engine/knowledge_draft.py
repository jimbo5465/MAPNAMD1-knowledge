"""
engine/knowledge_draft.py
مدل گزارش DANA و رندر آن.

دو بخش:
  ۱. build_report(...) — یک dict ساختاریافتهٔ استاندارد می‌سازد که ساختار
     هشت‌بخشی پیش‌نویس DANA (dana-draft.md §8) را پیاده می‌کند:
     اطلاعات ثبت / محتوا / فراداده / منابع / وضعیت QA / بازبینی اپراتور /
     موارد حل‌نشده / چک‌لیست نهایی اپراتور.
  ۲. render_text(report) — همان مدل را به متن قابل‌کپی برای تلگرام تبدیل می‌کند.

فایل‌های PDF و DOCX از همین مدل گزارش در engine/knowledge_render.py ساخته
می‌شوند — تا خروجی متن/PDF/Word همیشه یکسان باشد.
"""

from __future__ import annotations

from engine.knowledge_ai import FIELD_SCHEMAS, TYPE_LABELS

_SEPARATOR = "─" * 40

_NOT_PROVIDED = "[اختیاری - ارائه نشده]"
_OPERATOR_REQUIRED = "[ورودی اپراتور الزامی]"

# چک‌لیست نهایی اپراتور — مطابق dana-draft.md §6
_CHECKLIST = [
    "نوع دانش تأیید شد",
    "درخت دانش تأیید شد",
    "پروژه تأیید شد",
    "محدوده سازمانی تأیید شد",
    "سطح دسترسی تأیید شد",
    "همکاران تأیید شدند",
    "محتوا بازبینی شد",
    "فایل پیوست بازبینی شد",
    "هشتگ‌ها بازبینی شدند",
    "مسائل QA حل شدند",
    "پیش‌نویس نهایی برای ثبت در DANA تأیید شد",
]


def _lesson_description(fields: dict, raw_description: str | None) -> str:
    """شرح درس آموخته — به ترتیب منطقی Context→Problem→Action→Result→Lesson."""
    parts = []
    for key in ("context", "problem", "cause", "action", "result", "lesson"):
        value = fields.get(key)
        if value:
            parts.append(f"{FIELD_SCHEMAS['lesson'][key]}: {value}")
    if parts:
        return "\n".join(parts)
    return (raw_description or "").strip() or _NOT_PROVIDED


def build_report(
    *,
    knowledge_type: str,
    title: str,
    fields: dict,
    hashtags: list[str] | None,
    impact_type: str | None,
    project_name: str | None = None,
    reporter_name: str,
    reporter_title: str | None,
    reported_date: str,
    kn_number: str | None = None,
    raw_description: str | None = None,
    attachments: list[str] | None = None,
    polished: dict | None = None,
    tree_path: list[str] | None = None,
    org_metadata: dict | None = None,
    qa_notes: list[str] | None = None,
) -> dict:
    """
    مدل گزارش DANA را میسازد.

    polished: خروجی فیلدبهفیلد polish_dana_draft (بازنویسی حرفهای محتوا).
    qa_notes: هشدارهای QA سبک (غیرمسدودکننده) برای نمایش در بخش وضعیت QA.

    خروجی (dict):
        {
            "title", "type", "type_label",
            "qa_status", "qa_notes", "operator_review",
            "content":  [(فیلد، مقدار), ...],
            "metadata": [(فیلد، مقدار), ...],
            "resources": [str, ...],
            "unresolved": [str, ...],
            "checklist": [str, ...],
            "footer": str,
        }
    """
    type_label = TYPE_LABELS.get(knowledge_type, knowledge_type)
    hashtag_text = " ".join(f"#{h}" for h in hashtags) if hashtags else ""
    org = org_metadata or {}
    pol = polished or {}

    # ── محتوا — فیلدهای فرم DANA به ازای هر نوع ─────────────────────────────
    # فیلدهای ستارهدار فرم در صورت خالی بودن با _OPERATOR_REQUIRED علامت
    # میخورند؛ متن صیقلخورده AI فقط جایگزین همان فیلد میشود، نه ادغام.
    content: list[tuple[str, str]] = []
    if knowledge_type == "lesson":
        content.append(("عنوان", title or _OPERATOR_REQUIRED))
        desc = pol.get("polished_description") or _lesson_description(fields, raw_description)
        content.append(("شرح درس آموخته", desc or _OPERATOR_REQUIRED))
        content.append(("نتیجه اجرا", fields.get("result") or _OPERATOR_REQUIRED))
        if fields.get("recommendation"):
            content.append(("توصیه", fields["recommendation"]))
    elif knowledge_type == "suggestion":
        content.append(("عنوان پیشنهاد", title or _OPERATOR_REQUIRED))
        # فیلدهای رسمی فرم دانا — همیشه جدا نمایش داده میشوند (بدون ادغام روایی)
        content.append((
            "وضع موجود",
            pol.get("polished_current_state") or fields.get("current_state") or _OPERATOR_REQUIRED,
        ))
        content.append((
            "پیشنهاد بهبود",
            pol.get("polished_proposal") or fields.get("proposal") or _OPERATOR_REQUIRED,
        ))
        content.append(("تاثیر اجرای پیشنهاد", impact_type or _OPERATOR_REQUIRED))
        # پیشنهاد پیادهسازی‌نشده است → نتایج = اثر مورد انتظار با علامت
        expected = fields.get("expected_impact")
        if expected:
            content.append(("نتایج حاصل از اجرای پیشنهاد", f"{expected} (اثر مورد انتظار — تأیید نشده)"))
        else:
            content.append(("نتایج حاصل از اجرای پیشنهاد", _OPERATOR_REQUIRED))
    else:  # explicit
        content.append(("عنوان", title or _OPERATOR_REQUIRED))
        desc = pol.get("polished_description") or fields.get("description") or (raw_description or "").strip()
        content.append(("توضیحات", desc or _OPERATOR_REQUIRED))
        subtype_value = (fields.get("subtype") or "").strip()
        content.append(("زیرنوع دانش صریح", subtype_value or "[پیشنهادی — انتخاب نشده]"))

    # ── فراداده ────────────────────────────────────────────────────────────
    # درخت دانش: اگر مسیر انتخاب شده باشد، نشان داده میشود؛ وگرنه placeholder.
    if tree_path:
        tree_display = " > ".join(tree_path)
        tree_value = tree_display
    else:
        tree_value = f"[نیازمند انتخاب و تأیید اپراتور] {_OPERATOR_REQUIRED}"

    metadata: list[tuple[str, str]] = [
        ("درخت دانش", tree_value),
        ("پروژه", project_name or _NOT_PROVIDED),
        ("گزارش‌دهنده", reporter_name + (f" — {reporter_title}" if reporter_title else "")),
        ("تاریخ ثبت", reported_date or _NOT_PROVIDED),
        ("شماره ثبت", kn_number or "[پیش‌نمایش — قبل از ثبت نهایی]"),
        ("سطح دسترسی", "عادی"),
        ("همکاران", org.get("colleagues") or _NOT_PROVIDED),
        ("هشتگ‌ها", hashtag_text or _OPERATOR_REQUIRED),
        ("فایل پیوست", "[عکس‌ها آماده برای بارگذاری — نه بارگذاری‌شده]" if attachments else _NOT_PROVIDED),
    ]
    if knowledge_type == "lesson":
        # دستورالعمل و فرایندها: اختیاری است و پرسیده نمیشود — فقط جای آن در فرم باشد
        metadata.insert(1, ("دستورالعمل و فرایندها", _NOT_PROVIDED))
        metadata.insert(3, ("محدوده سازمانی (حیطه)", org.get("scope") or _NOT_PROVIDED))
    elif knowledge_type == "suggestion":
        metadata.append(("کمیته تخصصی", org.get("committee") or _OPERATOR_REQUIRED))
        # طبق فرم دانا این فیلد باید خالی بماند — پرسیده نمیشود و پر نمی شود
        metadata.append(("بذر پیشنهاد", "— (طبق فرم خالی می‌ماند)"))
    if knowledge_type == "explicit":
        metadata.insert(2, ("محدوده سازمانی (حیطه)", org.get("scope") or _NOT_PROVIDED))

    # ── منابع ──────────────────────────────────────────────────────────────
    resources: list[str] = []
    if attachments:
        for i, name in enumerate(attachments, start=1):
            resources.append(f"{i}. {name} (آماده برای بارگذاری)")
    else:
        resources.append("پیوستی ارائه نشده است.")

    # ── موارد حل‌نشده ──────────────────────────────────────────────────────
    unresolved: list[str] = []
    if not tree_path:
        unresolved.append("انتخاب و تأیید نهایی درخت دانش.")
    if knowledge_type == "lesson" and not project_name:
        unresolved.append("تعیین پروژه (فیلد الزامی فرم دانا برای درس آموخته).")
    if not org.get("colleagues"):
        unresolved.append("تأیید فهرست همکاران درگیر (اختیاری).")
    if knowledge_type == "suggestion":
        if not org.get("committee"):
            unresolved.append("تأیید کمیته تخصصی پیشنهادی.")
        if not impact_type:
            unresolved.append("تعیین تاثیر اجرای پیشنهاد (کیفی/کمی).")
    if knowledge_type == "explicit":
        if not org.get("scope"):
            unresolved.append("تعیین محدوده سازمانی (اختیاری).")
        if not (fields.get("subtype") or "").strip():
            unresolved.append("تعیین زیرنوع دانش صریح (کتاب/مقاله/لینک/...).")
    # اگر همه چیز پر شده، یک مورد نمادین
    if not unresolved:
        unresolved.append("هیچ مورد حل‌نشده‌ای باقی نمانده است.")

    notes = [str(n) for n in (qa_notes or []) if str(n).strip()]
    qa_status = f"نیازمند بازبینی ({len(notes)} مورد)" if notes else "نیازمند بازبینی"

    return {
        "title": title,
        "type": knowledge_type,
        "type_label": type_label,
        "qa_status": qa_status,
        "qa_notes": notes,
        "operator_review": "الزامی",
        "content": content,
        "metadata": metadata,
        "resources": resources,
        "unresolved": unresolved,
        "checklist": _CHECKLIST,
        "footer": (
            "تولید شده توسط ربات ثبت دانش مپنا توسعه — "
            "پیش‌نویس برای بازبینی و تأیید انسانی؛ ثبت نهایی در DANA بر عهده اپراتور است."
        ),
    }


# ══════════════════════════════════════════════════════════════════════════════
# عنوان نمایشی رکورد دانش (مشترک بین ربات‌ها و وب‌اپ)
# ══════════════════════════════════════════════════════════════════════════════

def _clean_md_line(line: str) -> str:
    """حذف مارک‌داون و کاراکترهای تزئینی از یک سطر."""
    return line.replace("*", "").replace("_", " ").strip()


def extract_title_from_draft(draft: str) -> str:
    """
    اولین مقدار معنادار بخش «محتوا» را از متن پیش‌نویس DANA برمی‌گرداند.
    (هدرِ «📄 پیش‌نویس ثبت دانش در DANA» و خطوط جداکننده نادیده گرفته می‌شود)
    """
    try:
        lines = draft.splitlines()
        start = 0
        for i, ln in enumerate(lines):
            if "محتوا" in ln:
                start = i + 1
                break
        for ln in lines[start:]:
            t = _clean_md_line(ln)
            if not t:
                continue
            if t.startswith("▫️"):
                _, _, val = t.partition(":")
                val = val.strip()
                if val and val != "—":
                    return val[:80]
                continue
            if t.startswith("────") or t.startswith("منابع") or t.startswith("پیوست"):
                break
            return t[:80]
    except Exception:
        pass
    return ""


def entry_display_title(entry: dict) -> str:
    """
    عنوان نمایشی یک رکورد دانش:
      ۱. fields_json.title  ۲. اولین سطر raw_description  ۳. بخش محتوای draft_text
    """
    fields = entry.get("fields_json") or {}
    if isinstance(fields, dict):
        t = str(fields.get("title") or "").strip()
        if t:
            return " ".join(t.split())[:80]
    raw = entry.get("raw_description") or ""
    for ln in raw.splitlines():
        c = _clean_md_line(ln)
        if c:
            return " ".join(c.split())[:80]
    draft = entry.get("draft_text") or ""
    t = extract_title_from_draft(draft)
    return (" ".join(t.split())[:80]) if t else "بدون عنوان"


def render_text(report: dict) -> str:
    """مدل گزارش را به متن قابل‌کپی برای تلگرام تبدیل می‌کند."""
    lines: list[str] = []

    # اطلاعات ثبت
    lines.append("📄 *پیش‌نویس ثبت دانش در DANA*")
    lines.append(_SEPARATOR)
    lines.append(f"نوع دانش: {report['type_label']}")
    lines.append(f"وضعیت QA: {report['qa_status']}")
    lines.append(f"بازبینی اپراتور: {report['operator_review']}")
    lines.append("")

    # محتوا
    lines.append("────── *محتوا* ──────")
    for label, value in report["content"]:
        lines.append(f"▫️ *{label}*: {value}")
    lines.append("")

    # فراداده
    lines.append("────── *فراداده* ──────")
    for label, value in report["metadata"]:
        lines.append(f"• {label}: {value}")
    lines.append("")

    # منابع
    lines.append("────── *منابع* ──────")
    lines.append("پیوست‌ها:")
    for r in report["resources"]:
        lines.append(f"  {r}")
    lines.append("")

    # QA
    lines.append("────── *وضعیت QA* ──────")
    lines.append(f"وضعیت کلی: {report['qa_status']}")
    for note in report.get("qa_notes") or []:
        lines.append(f"⚠️ {note}")
    if not report.get("qa_notes"):
        lines.append("مسئلهٔ حیاتی یافت نشد؛ موارد حل‌نشده را بازبینی کنید.")
    lines.append("")

    # موارد حل‌نشده
    lines.append("────── *موارد حل‌نشده* ──────")
    for item in report["unresolved"]:
        lines.append(f"• {item}")
    lines.append("")

    # چک‌لیست
    lines.append("────── *چک‌لیست نهایی اپراتور* ──────")
    for item in report["checklist"]:
        lines.append(f"[ ] {item}")
    lines.append("")
    lines.append(_SEPARATOR)
    lines.append(report["footer"])

    return "\n".join(lines)
