# WelderBot — Knowledge Registration Phase 3 Plan

> طراحی و پلن پیادهسازی فاز۳ ثبت دانش سازمانی — شامل موتور مصاحبه با AI، پاس نهایی polish، تنظیمات سازمانی، و درخت دانش رسمی.
> این فاز بر فاز۱ (ثبت دانش پایه) و فاز۲ (خروجی PDF/DOCX) بنا میشود.
>
> **وضعیت تصمیمات**: همهٔ تصمیمات معماری نهایی شدهاند (مرداد ۱۴۰۵).
> **وضعیت اجرا**: هنوز شروع نشده.
> **هدف**: دانش سازمانی توسط اپراتور از طریق مکالمه با AI یا فرم دستی ثبت شود و خروجی استاندارد DANA تولید شود.

---

## ۱. وضعیت فعلی پروژه (فاز۱ + فاز۲ — انجام شده)

### برنچ و مسیر
- شاخهٔ توسعه: `feature/knowledge-registration`
- مسیر کلون: `C:\Users\SHATER~2\AppData\Local\Temp\opencode\welderbot`
- تغییرات فاز۱+۲ **commit نشدهاند** (در انتظار تأیید کاربر).

### فایل‌های فعلی مرتبط با دانش
| فایل | توضیح |
|---|---|
| `engine/knowledge_ai.py` | `extract_fields()` (تک‌نوبت)، `FIELD_SCHEMAS` (فیلد فعلی)، `TYPE_LABELS`، classification + impact_type |
| `engine/knowledge_draft.py` | `build_report()` + `render_text()` — مدل گزارش هشت‌بخشی |
| `engine/knowledge_render.py` | `render_dana_pdf()` + `render_dana_docx()` — دیتا-درایو |
| `engine/knowledge_numbering.py` | `generate_knowledge_number(project_id)` — الگوی `KN-<کد>-<سال>-<سریال>` |
| `db/init.py` | جداول `knowledge_entries` + `knowledge_photos` + indexes + migration |
| `db/models.py` | CRUD: `add_knowledge_entry`، `set_knowledge_fields`، `submit_knowledge_entry(id, kn, pdf_path=None, docx_path=None)`، `set_knowledge_inactive`، `list_knowledge_entries`، `list_knowledge_entries_by_project`، `add_knowledge_photo`، `list_knowledge_photos`، `get_knowledge_entry_by_id` |
| `handlers/knowledge.py` | ConversationHandler فعلی: `KN_PROJECT → KN_CONTRACTOR → KN_REPORTER_NAME → KN_REPORTER_TITLE → KN_TYPE → KN_DESCRIPTION → KN_TYPE_CONFIRM → KN_FIELD_ANSWER → KN_PHOTOS → KN_DATE → KN_PREVIEW` (range(11)) |
| `handlers/keyboards.py` | دکمهٔ «📝 ثبت دانش/تجربه سازمانی» با callback `kn:new` |
| `config.py` | `KNOWLEDGE_AI_BASE_URL`, `KNOWLEDGE_AI_API_KEY`, `KNOWLEDGE_AI_MODEL`, `KNOWLEDGE_AI_TIMEOUT`, `KN_PHOTO_PATH`, `KN_OUTPUT_PATH` |
| `requirements.txt` | `python-telegram-bot==20.7`, `jdatetime`, `openpyxl`, `Pillow`, `httpx~=0.25.2`, `reportlab`, `arabic-reshaper`, `python-bidi`, `python-docx` |
| `test_knowledge_phase2.py` | ۱۳ تست PASS (DB, migration, draft, render, AI parsing, handler patterns) |
| `CONTRACTS.md` | مستندسازی APIهای فعلی |

### الگوهای کدنویسی پروژه
- زبان: فارسی برای پیام‌های کاربر و commentها، انگلیسی برای نام‌های فنی
- Type hints در همهٔ توابع جدید (`from __future__ import annotations`)
- Docstring به فارسی، خلاصه، با مثال در صورت نیاز
- ماژول‌های engine بدون import از handler؛ handlerها از engine و db استفاده میکنند
- تمام stateها به‌عنوان ثابت‌های ماژل level تعریف میشوند: `(STATE1, STATE2, ...) = range(N)`
- پیام‌های خطا: «❌ ...»، موفقیت: «✅ ...»، هشدار: «⚠️ ...»
- کلیدهای callback با پیشوند `kn_` یا `kn:` (برای legacy)

---

## ۲. تصمیمات معماری (نهایی شده)

### ۲.۱ حذف project/contractor از فلوی ورود
- `KN_PROJECT` و `KN_CONTRACTOR` **حذف** میشوند.
- DB: `project_id` و `contractor_id` به **nullable** تبدیل میشوند (با FK باقی میماند ولی اختیاری).
- اگر پروژه/پیمانکار در متن دانش ذکر شده، AI در مرحلهٔ polish آن را استخراج میکند (متادیتا اختیاری).
- اگر ذکر نشده، در گزارش DANA عبارت `[اختیاری - ارائه نشده]` نمایش داده میشود.

### ۲.۲ دو روش ثبت
- **روش۱ (دستی)**: اپراتور متن آزاد دانش را وارد میکند، AI یکبار استخراج میکند، فیلدهای ناقص یکییکی پرسیده میشوند، سپس AI polish نهایی فرم DANA را میسازد.
- **روش۲ (مصاحبه با AI)**: AI از ابتدا با اپراتور مصاحبه میکند، سؤال به سؤال اطلاعات را جمع میکند.
- هر دو روش از همان state `KN_TYPE` شروع میشوند (اپراتور نوع را انتخاب میکند).
- هر دو روش در `KN_FINAL_ASSEMBLE` به هم میرسند (AI polish).

### ۲.۳ تنظیمات سازمانی
- بعد از polish، قبل از preview، یک state `KN_ORG_META` وجود دارد که در آن اپراتور متادیتای سازمانی را تنظیم میکند:
  - **درخت دانش** (هر سه نوع): هیبرید (پیشنهاد AI + drill-down + تایپ + skip)
  - **کمیته تخصصی** (فقط suggestion): متن اختیاری
  - **بذر پیشنهاد** (فقط suggestion): متن اختیاری
  - **همکاران** (هر سه): متن اختیاری
  - **هشتگها** (هر سه): ویرایش/تأیید پیشنهاد AI
  - **محدوده سازمانی** (فقط explicit): متن اختیاری
- همهٔ موارد اختیاری؛ یک دکمهٔ «✓ پایان تنظیمات» + «⏭ فعلاً پر نمیکنم».

### ۲.۴ ویرایش پس از preview
- سه دکمهٔ همزمان در KN_PREVIEW: `✏️ ویرایش` / `📎 ضمیمهٔ عکس/مدرک` / `✅ تأیید و ثبت نهایی`
- `✏️ ویرایش` → KN_FIELD_EDIT (فهرست همهٔ فیلدهای نوع انتخابی + عنوان + هشتگ) → کلیک روی هرکدام → ویرایش متنی → بازگشت به KN_FIELD_EDIT (تا زمانی که کاربر دکمهٔ `↩️ بازگشت به پیشنمایش` بزند).

### ۲.۵ مصاحبه (روش۲)
- شروع: AI **اولین سؤال** را میپرسد (نه پیام آغازین از کاربر).
- پایان: هم AI میتواند `done: true` برگرداند، هم اپراتور دکمهٔ `✓ پایان مصاحبه` بزند.
- ویرایش میانی: **خیر** — فقط در KN_FIELD_EDIT بعد از پایان مصاحبه.
- سقف نوبت: **ندارد** (متن ارزان است).
- اگر AI سؤال را بیش از۲ بار تکرار کند → پیشنهاد skip یا پر کردن دستی.
- اگر AI در یک نوبت خطا/timeout بدهد → fallback به پرسش دستی همان فیلد (مکانیکی).
- Resume از DB بعد از restart ربات: **بله**.

### ۲.۶ درخت دانش
- هیبرید: ۴ دکمه در ابتدا
  - `💡 پیشنهاد AI` → ۳ پیشنهاد برتر با درصد اطمینان
  - `🔍 انتخاب دستی از درخت` → drill-down ۴ سطحی
  - `✏️ تایپ مسیر کامل` → ورودی متنی
  - `⏭ بعداً در DANA تکمیل میشود`
- اگر AI غیرفعال: فقط drill-down + تایپ + skip (بدون دکمهٔ AI).

---

## ۳. State machine کامل (فاز۳)

```python
(
    KN_MODE_SELECT,       # زیرمنو: روش دستی / مصاحبه
    KN_TYPE,              # ۳ دکمه: lesson/suggestion/explicit
    KN_REPORTER_NAME,     # نام گزارش‌دهنده
    KN_REPORTER_TITLE,    # سمت (skippable)

    # روش۱ (manual):
    KN_DESCRIPTION,       # متن آزاد → AI extract
    KN_FIELD_ANSWER,      # پرسش فیلد ناقص (متن یا impact_type)

    # روش۲ (interview):
    KN_INTERVIEW_FRAMEWORK,  # نمایش چارچوب راهنما
    KN_INTERVIEW_LOOP,    # حلقهٔ مصاحبه (AI سؤال/پاسخ/done)

    # مشترک:
    KN_FINAL_ASSEMBLE,    # AI polish (narrative + hashtags + project/contractor)
    KN_ORG_META,          # تنظیمات سازمانی (درخت، کمیته، بذر، همکاران، هشتگ، محدوده)
    KN_TREE,              # انتخاب درخت دانش (AI/drill-down/type/skip)
    KN_PREVIEW,           # پیشنمایش + ۳ دکمه
    KN_FIELD_EDIT,        # ویرایش فیلد از preview
    KN_PHOTOS,            # عکس/مدرک
    KN_DATE,              # تاریخ ثبت
    KN_FINISH,            # ثبت نهایی + ساخت PDF/DOCX + ارسال
) = range(17)
```

**نکات state machine:**
- پروژه و پیمانکار **حذف** شدند.
- `KN_TYPE_CONFIRM` حذف میشود (دیگر نیازی به تأیید نوع توسط AI نیست؛ اپراتور مستقیماً انتخاب میکند).
- flow ادغام: `KN_FIELD_ANSWER` (روش۱) و `KN_INTERVIEW_LOOP` (روش۲) هر دو به `KN_FINAL_ASSEMBLE` میروند.

---

## ۴. تغییرات DB (دقیق)

### ۴.۱ ستون‌های nullable
```sql
-- project_id و contractor_id از NOT NULL به nullable تغییر میکنند.
-- در SQLite باید table را بازسازی کرد (ALTER COLUMN وجود ندارد).
PRAGMA foreign_keys = OFF;

CREATE TABLE knowledge_entries_new (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    kn_number         TEXT UNIQUE,
    project_id        INTEGER REFERENCES projects(id),  -- nullable
    contractor_id     INTEGER REFERENCES contractors(id), -- nullable
    status            TEXT NOT NULL DEFAULT 'draft'
                       CHECK (status IN ('draft', 'submitted')),
    knowledge_type    TEXT NOT NULL
                       CHECK (knowledge_type IN ('lesson', 'suggestion', 'explicit')),
    reporter_name     TEXT NOT NULL,
    reporter_title    TEXT,
    reported_by       INTEGER NOT NULL REFERENCES users(id),
    raw_description   TEXT,
    fields_json       TEXT,
    draft_text        TEXT,
    reported_date     TEXT,
    submitted_at      TEXT,
    pdf_path          TEXT,
    docx_path         TEXT,
    is_active         INTEGER NOT NULL DEFAULT 1
                       CHECK (is_active IN (0, 1)),
    created_at        TEXT NOT NULL,
    interview_history_json TEXT,
    tree_path_json    TEXT,
    org_metadata_json TEXT,
    extra_data        TEXT
);

INSERT INTO knowledge_entries_new
    SELECT id, kn_number, project_id, contractor_id, status, knowledge_type,
           reporter_name, reporter_title, reported_by, raw_description,
           fields_json, draft_text, reported_date, submitted_at, pdf_path,
           docx_path, is_active, created_at, NULL, NULL, NULL, extra_data
    FROM knowledge_entries;

DROP TABLE knowledge_entries;
ALTER TABLE knowledge_entries_new RENAME TO knowledge_entries;

-- indexes از نو ساخته میشود (idempotent):
CREATE INDEX IF NOT EXISTS idx_knowledge_entries_project ON knowledge_entries(project_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_entries_status ON knowledge_entries(status);

PRAGMA foreign_keys = ON;
```

### ۴.۲ ستون‌های جدید
| ستون | نوع | اختیاری | محتوا |
|---|---|---|---|
| `interview_history_json` | TEXT | بله | لیست JSON از `{role, content, extracted}` |
| `tree_path_json` | TEXT | بله | JSON آرایه از نام نودها، مثلاً `["MAPNA Development","HSE Management","Safety"]` |
| `org_metadata_json` | TEXT | بله | JSON شامل `committee, seed, colleagues, scope, hashtags_override` |

### ۴.۳ migration در `db/init.py` (الگو)
```python
def _migrate_knowledge_phase3(cur) -> None:
    """idempotent migration برای فاز۳ دانش."""
    # بررسی وجود ستون interview_history_json
    cols = [row[1] for row in cur.execute("PRAGMA table_info(knowledge_entries)").fetchall()]
    if "interview_history_json" not in cols:
        cur.execute("ALTER TABLE knowledge_entries ADD COLUMN interview_history_json TEXT")
    if "tree_path_json" not in cols:
        cur.execute("ALTER TABLE knowledge_entries ADD COLUMN tree_path_json TEXT")
    if "org_metadata_json" not in cols:
        cur.execute("ALTER TABLE knowledge_entries ADD COLUMN org_metadata_json TEXT")
    # nullable کردن project_id/contractor_id اگر فعلی NOT NULL است
    # ... (rebuild table در صورت نیاز)
```

### ۴.۴ توابع جدید در `db/models.py`
- `set_knowledge_interview_history(knowledge_id, history: list)` → UPDATE
- `set_knowledge_tree_path(knowledge_id, path: list[str])` → UPDATE
- `set_knowledge_org_metadata(knowledge_id, org_data: dict)` → UPDATE
- `find_pending_knowledge_by_user(telegram_id) -> dict | None` → یافتن draft ناتمام برای resume

---

## ۵. ماژول‌های جدید

### ۵.۱ `engine/knowledge_tree.py`
**مسئولیت**: نگهداری درخت رسمی + navigation helpers.

```python
KNOWLEDGE_TREE: dict[str, dict] = {
    "MAPNA Development": {
        "HSE Management": {
            "Safety, Health and Environmental Risk Analysis": {},
            "Health, Safety and Environment": {
                "Occupational Health and Wellness": {},
                "Safety": {},
                "Environment": {},
            },
            ...
        },
        ...
    },
    ...
}

# توابع:
def get_children(path: list[str]) -> list[str]
def get_leaf_paths() -> list[list[str]]
def find_path_by_names(node_names: list[str]) -> list[str] | None
def render_path(path: list[str]) -> str  # "MAPNA > HSE > Safety"
def all_paths() -> list[list[str]]  # همهٔ مسیرهای کامل برای ارسال به AI
```

**دادهٔ درخت**: مستقیماً از `references/knowledge-tree.md` (skill) کپی میشود. ساختار ~۲۰۰ نود، ~۳۰۰ خط کد.

### ۵.۲ `engine/knowledge_interview.py`
**مسئولیت**: موتور مصاحبه، پاس polish، پیشنهاد درخت.

توابع اصلی:
```python
INTERVIEW_FRAMEWORKS: dict[str, list[str]]  # per-type لیست فیلدها به ترتیب

def build_interview_system_prompt(knowledge_type: str) -> str:
    """پرامپت سیستم برای مصاحبه."""

async def interview_next_turn(
    knowledge_type: str,
    history: list[dict],
    user_message: str,
) -> dict:
    """
    یک نوبت مکالمه با LLM.
    خروجی:
        {
            "extracted": dict[key, value] | None,  # فیلدهای استخراج‌شده از این پاسخ
            "ask": str | None,                    # سؤال بعدی
            "done": bool,                         # آیا مصاحبه تمام شد؟
            "title": str | None,                  # پیشنهاد عنوان
            "summary": str | None,                # خلاصه برای تأیید
        }
    """

async def polish_dana_draft(
    knowledge_type: str,
    fields: dict,
    raw_description: str | None,
    project_name: str | None,
    contractor_name: str | None,
) -> dict:
    """
    پاس polish نهایی — ساخت narrative حرفه‌ای + استخراج پروژه/پیمانکار + هشتگ.
    خروجی:
        {
            "narrative": str | None,
            "extracted_project": str | None,
            "extracted_contractor": str | None,
            "hashtags": list[str] | None,
            "title_suggestion": str | None,
        }
    اگر AI در دسترس نباشد → همه None (مکانیکی استفاده میشود).
    """

async def suggest_tree_paths(
    knowledge_type: str,
    fields: dict,
    raw_description: str | None,
    title: str | None,
    top_k: int = 3,
) -> list[dict]:
    """
    ۳ پیشنهاد برتر مسیر درخت دانش.
    خروجی: [{"path": [...], "confidence": 0.87, "reason": "..."}, ...]
    اگر AI در دسترس نباشد → [] (مکانیکی: اپراتور باید دستی انتخاب کند).
    """
```

### ۵.۳ تغییرات `engine/knowledge_draft.py`
- `build_report()`: اختیاری شدن `project_name`, `contractor_name` (None قابل قبول)
- افزودن پارامتر `narrative_override: str | None` — اگر از AI polish آمده، در بخش محتوا استفاده شود
- اگر narrative خالی/ندارد → همان مکانیکی فعلی

### ۵.۴ تغییرات `engine/knowledge_ai.py`
- حفظ توابع موجود (`extract_fields`, `FIELD_SCHEMAS`)
- **تغییر FIELD_SCHEMAS** به نسخهٔ کامل (دستهٔ الف + ب): افزودن `status, transferability` برای lesson، `subtype` برای explicit، `seed, committee` برای suggestion
- افزودن `_call_llm_simple_json(system, user)` → نسخهٔ ساده‌تر برای polish/suggest که فقط JSON برمیگرداند

---

## ۶. پرامپتهای AI (متن کامل)

### ۶.۱ پرامپت مصاحبه
```
تو یک مصاحبه‌گر دانش سازمانی هستی. یک اپراتور باتجربه در یک سایت صنعتی
(نیروگاه، پالایشگاه، کارخانه) پیش روی توست.
وظیفهٔ تو: کمک به او برای ثبت تجربه/دانشش مطابق ساختار استاندارد DANA.

نوع دانش: {type_label}

فیلدهایی که باید پر شوند ({n} مورد):
{field_list_with_labels}

رفتار:
1. در هر نوبت فقط یک سؤال کوتاه و دقیق بپرس.
2. سؤال‌ها باید به زبان فارسی و متناسب با زبان صنعتی باشند.
3. اگر پاسخ کاربر اطلاعات چند فیلد را پوشش داد، همه را در extracted بنویس.
4. اگر چیزی مبهم مانده، سؤال روشن‌کننده بپرس (نه سؤال جدید).
5. زمانی done: true برگردان که حداقل همهٔ فیلدهای ضروری پر شده باشند.

خروجی: فقط یک شیوهٔ JSON خالص (بدون backtick، بدون توضیح اضافه).
هر نوبت یکی از سه شکل:

1) پاسخ کاربر اطلاعاتی داد:
{
  "extracted": {"<key>": "<value>", ...},
  "ask": "<سؤال بعدی یا خاتمه>"
}

2) سؤال روشن‌کننده:
{"ask": "<سؤال توضیحی>"}

3) پایان مصاحبه:
{
  "done": true,
  "fields": {<همهٔ فیلدهای پرشده>},
  "title": "<پیشنهاد عنوان کوتاه>",
  "summary": "<یک‌خط خلاصه برای تأیید کاربر>"
}

قواعد:
- فقط از کلیدهای مجاز در فیلدها استفاده کن.
- مقادیر فارسی، طبیعی، خلاصه (۲ تا ۴ جمله).
- هیچ‌وقت فیلدی را حدس نزن.
```

### ۶.۲ پرامپت polish (نهایی)
```
تو یک دستیار آماده‌سازی فرم DANA هستی.
یک رکورد دانش سازمانی دریافت می‌کنی و باید آن را برای ثبت نهایی آماده کنی.

نوع دانش: {type_label}

فیلدهای پرشده:
{fields_json}

شرح اولیه (ممکن است خالی باشد):
{raw_description}

وظایف:
1. یک narrative حرفه‌ای به زبان فارسی بنویس که فیلدهای محتوایی را به شکل
   روان و ساختارمند ترکیب کند (مثلاً برای درس‌آموخته: زمینه → مشکل →
   اقدام → نتیجه → درس اصلی → توصیه).
2. اگر نام پروژه یا پیمانکار در شرح اولیه یا فیلدها ذکر شده، استخراج کن.
3. تا ۵ هشتگ مرتبط (فارسی، بدون #) پیشنهاد بده.
4. اگر عنوان فعلی ضعیف یا نامفهوم است، پیشنهاد بهتر بده.

خروجی JSON خالص:
{
  "narrative": "<متن narrative فارسی، ۳–۶ جمله>",
  "extracted_project": "<نام پروژه یا null>",
  "extracted_contractor": "<نام پیمانکار یا null>",
  "hashtags": ["برچسب۱", "برچسب۲", ...],
  "title_suggestion": "<پیشنهاد عنوان بهتر یا null>"
}

اگر چیزی برای گفتن نداری، مقدار null بگذار.
```

### ۶.۳ پرامپت پیشنهاد درخت دانش
```
تو یک دستیار طبقه‌بندی درخت دانش هستی.
یک دانش سازمانی دریافت میکنی و باید آن را در درخت رسمی سازمان قرار دهی.

نوع دانش: {type_label}
عنوان: {title}
فیلدها: {fields_json}
شرح: {raw_description}

درخت رسمی دانش (فقط این نودها مجازند — اختراع نکن، تغییر نام نده):
{tree_as_nested_yaml}

وظیفه: ۳ مسیر برتر (از ریشه تا برگ) پیشنهاد بده که بهترین تناسب را
با محتوای این دانش دارند.

خروجی JSON خالص:
{
  "suggestions": [
    {
      "path": ["MAPNA Development", "HSE Management", "Safety"],
      "confidence": 0.87,
      "reason": "<یک جمله فارسی دلیل>"
    },
    ... (۳ مورد، مرتب‌شده از بالاترین confidence)
  ]
}
```

---

## ۷. Schema خروجی AI (JSON)

### ۷.۱ مصاحبه — نوبت عادی
```json
{
  "extracted": {
    "context": "در پروژه نیروگاه سیکل ترکیبی شیراز، واحد۲، آبان ۱۴۰۴",
    "status": "جوشکاری در حال انجام بود ولی رطوبت بالا بود"
  },
  "ask": "چه مشکلی پیش آمد؟"
}
```

### ۷.۲ مصاحبه — سؤال توضیحی
```json
{"ask": "منظورتان از «مشکل جدی» دقیقاً چه اتفاقی بود؟"}
```

### ۷.۳ مصاحبه — پایان
```json
{
  "done": true,
  "fields": {
    "context": "...",
    "status": "...",
    "problem": "...",
    "cause": "...",
    "action": "...",
    "result": "...",
    "lesson": "...",
    "transferability": "...",
    "recommendation": "..."
  },
  "title": "کاهش ترک در جوشکاری سقف با کنترل رطوبت",
  "summary": "تجربهٔ رفع ترک در جوشکاری سقف به کمک کنترل رطوبت و دمای پیشگرم."
}
```

### ۷.۴ Polish
```json
{
  "narrative": "در پروژه نیروگاه سیکل ترکیبی شیراز، در آبان ۱۴۰۴ مشکل ترک در جوش سقف به دلیل رطوبت بالا رخ داد. پس از بررسی، با کنترل رطوبت محیط و افزایش دمای پیشگرم به ۱۵۰ درجه، مشکل برطرف شد.",
  "extracted_project": "نیروگاه سیکل ترکیبی شیراز",
  "extracted_contractor": null,
  "hashtags": ["جوشکاری", "ترک", "رطوبت", "پیشگرم"],
  "title_suggestion": null
}
```

### ۷.۵ پیشنهاد درخت
```json
{
  "suggestions": [
    {"path": ["MAPNA Development", "Execution Management and Supervision", "Civil Works"], "confidence": 0.78, "reason": "تجربه مربوط به جوشکاری در سایت اجراست"},
    {"path": ["MAPNA Development", "Project Management", "Project Quality Management"], "confidence": 0.61, "reason": "کنترل کیفیت در اجرا"},
    {"path": ["MAPNA Development", "Design and Engineering", "Mechanical Engineering"], "confidence": 0.42, "reason": "ممکن است به طراحی مکانیک مربوط باشد"}
  ]
}
```

---

## ۸. متن نمایشی Framework (KN_INTERVIEW_FRAMEWORK)

### ۸.۱ درس‌آموخته
```
📚 چارچوب راهنما — درس‌آموخته

محورهایی که در ادامه با هم مرور میکنیم:
 ۱) زمینه و بستر — کجا؟ کِی؟ چه پروژه‌ای؟
 ۲) وضعیت — قبل از اقدام چه خبرت بود؟
  ۳) مشکل یا فرصت
  ۴) علت یا عوامل مؤثر
  ۵) اقدام انجام‌شده — چه کاری کردید؟
  ۶) نتیجهٔ واقعی — چه اتفاقی افتاد؟
  ۷) درس اصلی آموخته‌شده
  ۸) قابلیت انتقال — در کجای دیگر هم کاربرد دارد؟
  ۹) توصیه برای دیگران

⏱ معمولاً ۹ سؤال کوتاه پرسیده میشود.
هر جا خواستید میتوانید با دکمهٔ «✓ پایان مصاحبه» زودتر خارج شوید.

[▶️ شروع مصاحبه]  [❌ انصراف]
```

### ۸.۲ پیشنهاد
```
💡 چارچوب راهنما — پیشنهاد

محورها:
  ۱) وضع موجود — الان چه خبرت است؟
  ۲) مشکل یا فرصت بهبود
  ۳) پیشنهاد بهبود — چه کنیم؟
  ۴) نتایج مورد انتظار (اگر اجرا شود چه میشود؟)
  ۵) تاثیر اجرا — کیفی یا کمی؟
  ۶) بذر پیشنهاد — ایده از کجا آمد؟
  ۷) کمیتهٔ تخصصی پیشنهادی
  ۸) همکاران درگیر

[▶️ شروع مصاحبه]  [❌ انصراف]
```

### ۸.۳ دانش صریح
```
📖 چارچوب راهنما — دانش صریح

محورها:
  ۱) نوع دانش — کتاب/مقاله/لینک/گزارش/استاندارد/پادکست/اختراع/مجله/...
  ۲) موضوع اصلی
  ۳) شرح کامل — چرا مهم است؟ چه یاد میدهد؟
  ۴) محدودهٔ سازمانی — به کدام بخش‌ها مربوط است؟

[▶️ شروع مصاحبه]  [❌ انصراف]
```

---

## ۹. Field labels (کلید → برچسب فارسی)

### ۹.۱ درس‌آموخته (lesson)
```python
LESSON_FIELDS = {
    "context": "زمینه و بستر",
    "status": "وضعیت",
    "problem": "مشکل یا فرصت",
    "cause": "علت یا عوامل مؤثر",
    "action": "اقدام انجام‌شده",
    "result": "نتیجهٔ واقعی",
    "lesson": "درس اصلی آموخته‌شده",
    "transferability": "قابلیت انتقال",
    "recommendation": "توصیه برای دیگران",
}
# impact_type مختص پیشنهاد است؛ برای lesson وجود ندارد
```

### ۹.۲ پیشنهاد (suggestion)
```python
SUGGESTION_FIELDS = {
    "current_state": "وضع موجود",
    "problem": "مشکل یا فرصت بهبود",
    "proposal": "پیشنهاد بهبود",
    "expected_impact": "نتایج مورد انتظار",
    # impact_type: دکمه‌ای است (کیفی/کمی) — جدا از fields dict
    "seed": "بذر پیشنهاد",
    "committee": "کمیتهٔ تخصصی",
    "colleagues": "همکاران درگیر",
}
```

### ۹.۳ دانش صریح (explicit)
```python
EXPLICIT_FIELDS = {
    "subtype": "نوع دانش (زیرنوع)",  # دکمه‌ای
    "subject": "موضوع",
    "description": "شرح کامل",
    "scope": "محدودهٔ سازمانی",
    "colleagues": "همکاران درگیر",
}
```

---

## ۱۰. Callback data (فهرست کامل)

### ۱۰.۱ منو و ورود
- `kn:new` — ورود به ثبت دانش (از منوی اصلی)
- `kn_mode:manual` — انتخاب روش دستی
- `kn_mode:interview` — انتخاب روش مصاحبه

### ۱۰.۲ نوع و گزارش‌دهنده
- `kn_type:lesson|suggestion|explicit` — انتخاب نوع دانش
- `kn_skip:title` — رد کردن سمت گزارش‌دهنده

### ۱۰.۳ مصاحبه (روش۲)
- `kn_interview:start` — شروع مصاحبه (بعد از framework)
- `kn_interview:done` — پایان مصاحبه توسط کاربر (در همه نوبت‌ها موجود)

### ۱۰.۴ فیلدها (روش۱)
- `kn_skip_field` — رد کردن فیلد ناقص جاری
- `kn_impact:کیفی` / `kn_impact:کمی` — تاثیر اجرای پیشنهاد

### ۱۰.۵ تنظیمات سازمانی (KN_ORG_META)
- `kn_org:tree` — ورود به تنظیم درخت دانش
- `kn_org:committee` — تنظیم کمیته تخصصی (فقط suggestion)
- `kn_org:seed` — تنظیم بذر پیشنهاد (فقط suggestion)
- `kn_org:colleagues` — تنظیم همکاران
- `kn_org:hashtags` — ویرایش هشتگ‌ها
- `kn_org:scope` — تنظیم محدوده سازمانی (فقط explicit)
- `kn_org:done` — پایان تنظیمات سازمانی → KN_PREVIEW
- `kn_org:skip` — رد کردن کل تنظیمات (خالی میماند)

### ۱۰.۶ درخت دانش (KN_TREE)
- `kn_tree:ai` — نمایش پیشنهادهای AI (اگر موجود)
- `kn_tree:ai:pick:<idx>` — انتخاب پیشنهاد شماره idx
- `kn_tree:nav` — شروع drill-down دستی از سطح۱
- `kn_tree:nav:<level>:<parent_idx>` — رفتن به سطح بعد (level: 1..4)
- `kn_tree:nav:back` — بازگشت یک سطح
- `kn_tree:nav:reset` — بازنشانی drill-down از سطح۱
- `kn_tree:confirm` — تأیید مسیر انتخاب‌شده در سطح برگ
- `kn_tree:type` — ورود به حالت تایپ مسیر
- `kn_tree:type_done` — تأیید مسیر تایپ‌شده
- `kn_tree:skip` — رد کردن درخت

### ۱۰.۷ ویرایش (KN_FIELD_EDIT)
- `kn_edit:back` — بازگشت به پیشنمایش
- `kn_edit:field:<key>` — ویرایش فیلد مشخص

### ۱۰.۸ پیوست و ثبت
- `kn_photos_done` — پایان عکس‌ها
- `kn_today` — تاریخ امروز
- `kn_finish` — ثبت نهایی

### ۱۰.۹ بازیابی (resume)
- `kn_resume:yes` — ادامه ثبت قبلی
- `kn_resume:no` — شروع ثبت جدید (soft-delete قبلی)

---

## ۱۱. Resume logic

### ۱۱.۱ تشخیص ثبت ناتمام
هنگام ورود کاربر به ثبت دانش (`kn:new`) یا `/start`:
1. `find_pending_knowledge_by_user(telegram_id)`:
   ```sql
   SELECT k.* FROM knowledge_entries k
   JOIN users u ON u.id = k.reported_by
   WHERE u.telegram_id = ?
     AND k.status = 'draft'
     AND k.kn_number IS NULL
     AND (k.interview_history_json IS NOT NULL
          OR k.raw_description IS NOT NULL)
   ORDER BY k.created_at DESC
   LIMIT 1;
   ```
2. اگر یافت شد → پیام «شما یک ثبت ناتمام دارید. ادامه بدهم؟»
 + `kn_resume:yes` / `kn_resume:no`
3. اگر نه → مستقیم به `KN_MODE_SELECT`

### ۱۱.۲ ادامه (resume)
1. بارگذاری fields، history، org_metadata، tree_path از DB به `context.user_data`
2. تشخیص آخرین state بر اساس اینکه کدام فیلدها پر شده‌اند:
   - اگر `interview_history_json` خالی → KN_INTERVIEW_LOOP (روش۲) یا KN_FIELD_ANSWER (روش۱)
   - اگر همه فیلدهای محتوایی پر و `org_metadata` خالی → KN_ORG_META
   - اگر `org_metadata` پر و `pdf_path` خالی → KN_PREVIEW
3. ارسال پیام مناسب بر اساس state بازسازی‌شده

---

## ۱۲. Edge cases

| سناریو | رفتار مورد انتظار |
|---|---|
| AI timeout (>60s) | fallback به پرسش دستی فیلد بعدی + لاگ warning |
| AI JSON نامعتبر | retry یکبار؛ اگر باز هم نامعتبر → fallback به مکانیکی |
| AI سؤال تکراری (بیش از۲ بار) | پیام «این سؤال قبلاً پرسیده شد. لطفاً پاسخ دهید یا رد کنید» |
| اپراتور `/cancel` | soft-delete draft + پاکسازی عکس‌های موقت + END |
| اپراتور `/start` | همان `/cancel` فعلی |
| پیام نامرتبط (متن خالی، عکس بجای متن) | در KN_FIELD_ANSWER و KN_INTERVIEW_LOOP: پیام «لطفاً فقط متن بفرستید» |
| پیام خیلی طولانی (>4000 کاراکتر) | truncate + پیام هشدار |
| اپراتور در حین مصاحبه عکس میفرستد | ذخیره نمیشود؛ پیام «عکس در این مرحله مجاز نیست» |
| درخت drill-down سطح۱ >۵۰ نود | (فعلاً نیست؛ در آینده pagination لازم) |
| `is_ai_enabled()` False و روش۲ | در KN_MODE_SELECT: فقط روش۱ نمایش داده شود (یا هردو با هشدار) |
| بیش از۱۵ ثبت draft باز برای یک کاربر | (فعلاً پیاده نمیشود؛ soft-delete قدیمی‌ترین) |
| Chat ID متفاوت با Telegram ID | همان پیام خطای فعلی (`get_user_by_user_data_id`) |

---

## ۱۳. پلن پیادهسازی مرحلهای

| مرحله | شرح | تخمین | پیشنیاز | تست |
|---|---|---|---|---|
| **۳a** | DB migration: nullable FK + ستون‌های جدید | ۲ ساعت | — | `test_migration_phase3.py` |
| **۳b** | `engine/knowledge_tree.py` + navigation helpers | ۲ ساعت | — | `test_knowledge_tree.py` |
| **۳c** | `engine/knowledge_interview.py`: پرامپت + parser + retry + `interview_next_turn` | ۵ ساعت | — | `test_interview_mocked.py` |
| **۳d** | `engine/knowledge_interview.py`: `polish_dana_draft` + `suggest_tree_paths` | ۳ ساعت | ۳c | `test_polish_mocked.py` |
| **۳e** | `engine/knowledge_draft.py`: اختیاری شدن project/contractor + narrative override | ۱ ساعت | ۳d | extension to phase2 test |
| **۳f** | `engine/knowledge_ai.py`: FIELD_SCHEMAS تکمیل + `_call_llm_simple_json` | ۱ ساعت | — | extension to phase2 test |
| **۳g** | `handlers/knowledge.py`: حذف KN_PROJECT/CONTRACTOR، اضافه KN_MODE، بازنویسی فلو | ۸ ساعت | ۳a-۳f | manual integration |
| **۳h** | `handlers/knowledge.py`: KN_INTERVIEW_FRAMEWORK + KN_INTERVIEW_LOOP | ۵ ساعت | ۳c, ۳g | mocked conversation test |
| **۳i** | `handlers/knowledge.py`: KN_ORG_META + KN_TREE (drill-down + AI suggestion) | ۶ ساعت | ۳b, ۳d, ۳g | mocked test |
| **۳j** | `handlers/knowledge.py`: KN_FIELD_EDIT + سه دکمهٔ preview | ۳ ساعت | ۳g | manual integration |
| **۳k** | Resume logic در `find_pending_knowledge_by_user` + `kn_resume:yes/no` | ۲ ساعت | ۳a | resume test |
| **۳l** | `db/models.py`: توابع جدید (interview_history, tree_path, org_metadata) | ۲ ساعت | ۳a | unit test |
| **۳m** | `CONTRACTS.md` + docstringها | ۱ ساعت | همهٔ بالا | — |
| **۳n** | End-to-end smoke test (AI mocked) | ۳ ساعت | همهٔ بالا | `test_knowledge_phase3.py` |

**مجموع تخمینی**: ~۴۴ ساعت (معادل۶ روز کاری). بستگی به سرعت AI و پیچیدگی تست.

---

## ۱۴. ریسک‌ها و راهکار

| ریسک | احتمال | تأثیر | راهکار |
|---|---|---|---|
| AI پاسخ JSON نامعتبر بدهد | متوسط | متوسط | پاراسر مقاوم + retry یکبار + fallback |
| AI در یک سؤال حلقه بیافتد | متوسط | کم | سقف۲ بار برای هر سؤال |
| مصاحبه بسیار طولانی شود | کم | متوسط | context window خلاصه‌سازی history هر۱۰ نوبت |
| Resume پس از restart پیچیده شود | متوسط | متوسط | فقط history + fields + tree_path ذخیره؛ state از داده‌ها بازسازی میشود |
| درخت دانش در drill-down برای سطوح پایین شلوغ شود | کم | کم | حداکثر سطوح موجود ۴ است؛ بیشترین تعداد فرزند ۱۱ (Electrical Engineering) — قابل مدیریت |
| AI polish فیلدها را خراب کند | کم | بال | polish اختیاری است؛ اگر JSON نامعتبر شد، narrative مکانیکی فعلی استفاده میشود |
| پیشنهاد درخت AI نود اشتباه پیشنهاد دهد | متوسط | متوسط | مهارت اجازهٔ اختراع نمیدهد؛ امتیاز confidence کمک میکند؛ اپراتور همیشه drill-down دستی دارد |
| هزینهٔ LLM زیاد شود | کم | متوسط | log token usage؛ سقف نوبت (اگر در آینده لازم شد) |

---

## ۱۵. سؤالات باز / کار آینده

1. آیا نیاز به **گزارش ماهانه** از دانش‌های ثبت‌شده هست؟ (خارج از scope فاز۳ فعلاً)
2. آیا **ادمین** باید بتواند draftهای ناتمام را ببیند/پاک کند؟ (خارج از scope فعلاً)
3. آیا اپراتور بتواند **نوع دانش را بعد از ثبت تغییر دهد**؟ (فعلاً خیر — soft-delete و ثبت مجدد)
4. آیا در آینده **جستجو** در دانش‌های ثبت‌شده لازم است؟ (بله، ولی فاز بعدی)
5. آیا **پشتیبانی از صدا** (Whisper یا OpenCode audio) لازم است؟ (فعلاً خیر)

---

## ۱۶. تغییرات breaking (برای کاربران فعلی)

- حذف انتخاب پروژه/پیمانکار از ثبت دانش — اگر ثبت‌های قبلی در DB موجود است، `project_id`/`contractor_id` آن‌ها nullable میشود ولی مقدار حفظ میشود (migration بدون از دست رفتن داده).
- callback_data های `kn_proj:*` و `kn_ctr:*` و `kn_type_keep` و `kn_type_switch:*` **حذف** میشوند.
- تمام handlerهای مرتبط با project/contractor **حذف** میشوند.

---

## ۱۷. منابع مرتبط

- مهارت: `C:\Users\shaterian_m\.config\opencode\skills\organizational-knowledge-skill\`
  - `references/lesson-learned.md §14`
  - `references/suggestion.md §15`
  - `references/explicit-knowledge.md`
  - `references/dana-draft.md §4/§6/§8`
  - `references/knowledge-classification.md`
  - `references/knowledge-tree.md`
  - `references/metadata.md`
- نمونه‌های خروجی PDF/Word: `scripts/make_dana_pdf.py`, `scripts/make_dana_docx.py` (مرجع منطقی — نه import مستقیم؛ در فاز۲ دیتا-درایو شدند)
- ربات فعلی: `C:\Users\SHATER~2\AppData\Local\Temp\opencode\welderbot`

---

**پایان پلن.** منتظر تأیید برای شروع فاز۳a.