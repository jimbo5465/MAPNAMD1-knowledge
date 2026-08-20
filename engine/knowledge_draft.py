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
    contractor_name: str | None = None,
    reporter_name: str,
    reporter_title: str | None,
    reported_date: str,
    kn_number: str | None = None,
    raw_description: str | None = None,
    attachments: list[str] | None = None,
    narrative_override: str | None = None,
    tree_path: list[str] | None = None,
    org_metadata: dict | None = None,
) -> dict:
    """
    مدل گزارش DANA را می‌سازد.

    خروجی (dict):
        {
            "title", "type", "type_label",
            "qa_status", "operator_review",
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

    # ── محتوا — فیلدهای فرم DANA به ازای هر نوع ─────────────────────────────
    # اگر narrative_override داده شده، در بخش اصلی محتوا استفاده میشود
    # (AI polish در فاز۳ آن را تولید میکند)؛ در غیر این صورت، مکانیکی.
    content: list[tuple[str, str]] = []
    if knowledge_type == "lesson":
        content.append(("عنوان", title or _NOT_PROVIDED))
        if narrative_override:
            content.append(("شرح درس آموخته", narrative_override))
        else:
            content.append(("شرح درس آموخته", _lesson_description(fields, raw_description)))
        content.append(("نتیجه اجرا", fields.get("result") or _NOT_PROVIDED))
        if fields.get("recommendation"):
            content.append(("توصیه", fields["recommendation"]))
    elif knowledge_type == "suggestion":
        content.append(("عنوان پیشنهاد", title or _NOT_PROVIDED))
        if narrative_override:
            content.append(("شرح پیشنهاد", narrative_override))
        else:
            content.append(("وضع موجود", fields.get("current_state") or _NOT_PROVIDED))
            content.append(("پیشنهاد بهبود", fields.get("proposal") or _NOT_PROVIDED))
        content.append(("تاثیر اجرای پیشنهاد", impact_type or _NOT_PROVIDED))
        # پیشنهاد پیادهسازی‌نشده است → نتایج = اثر مورد انتظار با علامت
        expected = fields.get("expected_impact")
        if expected:
            content.append(("نتایج حاصل از اجرای پیشنهاد", f"{expected} (اثر مورد انتظار — تأیید نشده)"))
        else:
            content.append(("نتایج حاصل از اجرای پیشنهاد", _NOT_PROVIDED))
    else:  # explicit
        content.append(("عنوان", title or _NOT_PROVIDED))
        if narrative_override:
            content.append(("شرح", narrative_override))
        else:
            content.append(("شرح", fields.get("description") or (raw_description or "").strip() or _NOT_PROVIDED))
        content.append((
            "زیرنوع دانش صریح",
            "[پیشنهادی — کتاب/محتوای آموزشی/لینک/گزارش بین‌المللی/پادکست/مقاله/اختراع/مجله/استاندارد]",
        ))

    # ── فراداده ────────────────────────────────────────────────────────────
    # درخت دانش: اگر مسیر انتخاب شده باشد، نشان داده میشود؛ وگرنه placeholder.
    if tree_path:
        tree_display = " > ".join(tree_path)
        tree_value = tree_display
    else:
        tree_value = f"[پیشنهادی — تعیین در opencode] {_OPERATOR_REQUIRED}"

    metadata: list[tuple[str, str]] = [
        ("درخت دانش", tree_value),
        ("پروژه", project_name or _NOT_PROVIDED),
        ("پیمانکار", contractor_name or _NOT_PROVIDED),
        ("گزارش‌دهنده", reporter_name + (f" — {reporter_title}" if reporter_title else "")),
        ("تاریخ ثبت", reported_date or _NOT_PROVIDED),
        ("شماره ثبت", kn_number or "[پیش‌نمایش — قبل از ثبت نهایی]"),
        ("سطح دسترسی", "عادی"),
        ("همکاران", org.get("colleagues") or _NOT_PROVIDED),
        ("هشتگ‌ها", hashtag_text or _NOT_PROVIDED),
        ("فایل پیوست", "[عکس‌ها آماده برای بارگذاری — نه بارگذاری‌شده]" if attachments else _NOT_PROVIDED),
    ]
    # متادیتای سازمانی اضافی بر اساس نوع
    if knowledge_type == "suggestion":
        metadata.append(("کمیته تخصصی", org.get("committee") or _NOT_PROVIDED))
        metadata.append(("بذر پیشنهاد", org.get("seed") or _NOT_PROVIDED))
    if knowledge_type == "explicit":
        metadata.append(("محدوده سازمانی", org.get("scope") or _NOT_PROVIDED))

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
        unresolved.append("انتخاب و تأیید نهایی درخت دانش (پیشنهادی: تعیین در opencode).")
    if not project_name:
        unresolved.append("تأیید نام رسمی پروژه (در متن دانش ذکر شده ولی به‌عنوان پروژه تأیید نشده).")
    if not contractor_name:
        unresolved.append("تأیید نام رسمی پیمانکار (در صورت مرتبط بودن).")
    if not org.get("colleagues"):
        unresolved.append("تأیید فهرست همکاران درگیر.")
    if knowledge_type == "suggestion":
        if not org.get("committee"):
            unresolved.append("تأیید کمیته تخصصی پیشنهادی.")
        if not org.get("seed"):
            unresolved.append("تعیین بذر پیشنهاد (ایده از کجا آمد).")
    if knowledge_type == "explicit":
        if not org.get("scope"):
            unresolved.append("تعیین محدوده سازمانی.")
        unresolved.append("تعیین زیرنوع دانش صریح (کتاب/مقاله/لینک/...).")
    # اگر همه چیز پر شده، یک مورد نمادین
    if not unresolved:
        unresolved.append("هیچ مورد حل‌نشده‌ای باقی نمانده است.")

    return {
        "title": title,
        "type": knowledge_type,
        "type_label": type_label,
        "qa_status": "نیازمند بازبینی",
        "operator_review": "الزامی",
        "content": content,
        "metadata": metadata,
        "resources": resources,
        "unresolved": unresolved,
        "checklist": _CHECKLIST,
        "footer": (
            "تولید شده توسط WelderBot (مطابق organizational-knowledge-skill) — "
            "پیش‌نویس برای بازبینی و تأیید انسانی؛ ثبت نهایی در DANA بر عهده اپراتور است."
        ),
    }


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
    lines.append(report["qa_status"] + " — " + "مسئلهٔ حیاتی یافت نشد؛ موارد حل‌نشده را بازبینی کنید.")
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
