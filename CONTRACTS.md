# WelderBot Internal Contracts

---

## Purpose

این فایل قراردادهای داخلی پروژه WelderBot را تعریف می‌کند — برای حفظ
سازگاری بین ماژول‌ها و جلوگیری از تغییرات ناسازگار در APIهای داخلی.
هر توسعه‌دهنده (انسان یا AI) قبل از ایجاد feature جدید باید این فایل
را مطالعه کند.

---

## Development Rules

1. هیچ feature نباید feature دیگری را خراب کند.
2. توابع public نباید بدون دلیل تغییر signature داشته باشند.
3. دسترسی مستقیم به دیتابیس خارج از `db/models.py` مجاز نیست.
4. هر feature جدید باید مستقل و قابل commit باشد.
5. هر تغییر در public API باید هم‌زمان در این فایل ثبت شود.

---

## Stable Modules

| Module | Description |
|---|---|
| `db/models.py` | Database Access Layer |
| `engine/qualification.py` | Qualification Engine |
| `engine/report_builder.py` | WPQ Excel Builder |
| `handlers/auth.py` | Authentication & Access Control |
| `handlers/keyboards.py` | Shared Telegram Keyboards |
| `forms/wqt_excel.py` | Excel Generator |

---

## Module Responsibilities

- **db/** — تمام عملیات SQLite و CRUD.
- **handlers/** — مدیریت گفتگوهای تلگرام و ارتباط با کاربر.
- **engine/** — پیاده‌سازی منطق استاندارد ASME Section IX.
- **forms/** — تولید فایل‌های Excel.
- **utils/** — توابع عمومی مورد استفاده در کل پروژه.

## Dependency Rules
handlers
↓
engine
↓
db
forms
↓
db
utils ← قابل استفاده در همه‌ی ماژول‌ها
---

## Public APIs — db/models.py

### User Management
- `add_user()`
- `get_user_by_telegram_id()`
- `list_users()`
- `set_user_inactive()`

### Access Control
- `register_pending_user(telegram_id, full_name, username) -> None`
- `list_pending_users(exclude_telegram_ids=None) -> list[dict]`
- `add_access_grant(telegram_id, level, granted_by, project_id=None, contractor_id=None) -> int`
- `get_access_grants_by_telegram(telegram_id, active_only=True) -> list[dict]`
- `revoke_access_grant(grant_id) -> None`
- `list_grants_by_project(project_id) -> list[dict]`

### Project Management
- `add_project(name) -> int` — بدون `contractor_id` (رابطه چند‌به‌چند است)
- `list_projects(active_only=True) -> list[dict]` — بدون پارامتر `contractor_id`
- `get_project_by_id(project_id) -> dict | None`
- `update_project_name(project_id, name) -> None`
- `set_project_inactive(project_id) -> None` — soft delete
- `reactivate_project(project_id) -> None`
- `get_project_stats(project_id) -> dict` — `{active_contractors: int, active_qualifications: int}`

### Contractor Management
- `add_contractor()`
- `list_contractors()`
- `get_contractor_by_id(contractor_id) -> dict | None`
- `get_contractor_by_name(name) -> dict | None`
- `update_contractor_name(contractor_id, name) -> None` — تغییر **سراسری**، روی همه‌ی پروژه‌های آن پیمانکار اثر می‌گذارد؛ فقط سطح ۱ باید صدا بزند

### Project ⇆ Contractor Relationship (`project_contractors`)
- `link_contractor_to_project(project_id, contractor_id, linked_by, label=None) -> int` — هم برای لینک اول، هم برای الحاق مجدد بعد از خاتمه
- `get_link_by_id(link_id) -> dict | None`
- `list_contractor_links_by_project(project_id, statuses=None) -> list[dict]`
- `list_contractors_by_project(project_id, active_only=True) -> list[dict]`
- `list_projects_by_contractor(contractor_id, active_only=True) -> list[dict]`
- `update_link_label(link_id, label) -> None` — برچسب **محلیِ پروژه**، نه نام پیمانکار
- `terminate_link_direct(link_id, terminated_by) -> None` — فقط سطح ۱
- `request_terminate_link(link_id, requested_by) -> None` — سطح ۲
- `approve_terminate_link(link_id, approved_by) -> None` — فقط سطح ۱
- `reject_terminate_link(link_id, reason) -> None` — فقط سطح ۱
- `list_pending_termination_requests() -> list[dict]`
- `is_link_active(project_id, contractor_id) -> bool` — API آماده برای گیت‌کردن «ثبت فعالیت جدید» در ماژول‌های فعالیتی آینده (جوش/رنگ/فیتینگ)؛ فعلاً هیچ‌جا صدا زده نمی‌شود، فقط قرارداد است.

> **قانون:** هیچ‌کدام از عملیات پروژه/پیمانکار hard-delete نیستند. `set_project_inactive` فقط `is_active=0` می‌کند؛ رکوردهای وابسته‌ی `project_contractors` و `qualifications` دست‌نخورده باقی می‌مانند.
>
> **قانون داده:** یک جفت `(project_id, contractor_id)` می‌تواند چند رکورد تاریخی در `project_contractors` داشته باشد؛ فقط یک رکورد `status='active'` برای هر جفت مجاز است — تضمین‌شده با partial UNIQUE INDEX در دیتابیس، نه فقط منطق برنامه.

### Welder Management
- `add_welder()`
- `get_welder_by_id()`
- `get_welder_by_national_id()`
- `list_welders_by_contractor()`
- `search_welders()`
- `update_welder()`
- `update_welder_photo()`
- `set_welder_inactive()`

### Qualification Management
- `add_qualification()`
- `get_qualification_by_id()`
- `list_qualifications_by_welder()`
- `get_expiring_qualifications()`
- `set_qualification_inactive()`

### Reference Data
- `list_materials()`
- `list_fillers()`

### Knowledge Management (فاز دانش سازمانی — `knowledge_entries` / `knowledge_photos`)
- `add_knowledge_entry(project_id, contractor_id, reported_by, knowledge_type, reporter_name, reporter_title=None, raw_description=None, fields=None, draft_text=None, reported_date=None, extra_data=None) -> int`
  - `project_id` و `contractor_id` از فاز۳ اختیاریاند (نوع `int | None`؛ NULL در DB).
  - `knowledge_type` ∈ `lesson | suggestion | explicit`؛ وضعیت اولیه `draft`
- `get_knowledge_entry_by_id(knowledge_id) -> dict | None` — LEFT JOIN با projects/contractors؛ `fields_json`/`extra_data`/`tree_path_json`/`org_metadata_json` دسریالایز می‌شوند
- `set_knowledge_fields(knowledge_id, fields, draft_text=None) -> None`
- `submit_knowledge_entry(knowledge_id, kn_number, pdf_path=None, docx_path=None) -> None` — `draft` → `submitted` + شماره + `submitted_at` + مسیرهای فایل خروجی (اختیاری)
- `set_knowledge_inactive(knowledge_id) -> None` — soft delete
- `list_knowledge_entries(active_only=True) -> list[dict]`
- `list_knowledge_entries_by_project(project_id, statuses=None, active_only=True) -> list[dict]`
- `add_knowledge_photo(knowledge_id, path) -> int`
- `list_knowledge_photos(knowledge_id) -> list[dict]`

**توبع جدید فاز۳:**
- `set_knowledge_interview_history(knowledge_id, history: list[dict]) -> None`
- `get_knowledge_interview_history(knowledge_id) -> list[dict]`
- `set_knowledge_tree_path(knowledge_id, path: list[str]) -> None`
- `get_knowledge_tree_path(knowledge_id) -> list[str]`
- `set_knowledge_org_metadata(knowledge_id, org_data: dict) -> None`
- `get_knowledge_org_metadata(knowledge_id) -> dict`
- `find_pending_knowledge_by_user(telegram_id) -> dict | None` — آخرین draft ناتمام متعلق به کاربر (برای resume)

> **قانون:** شمارهٔ `KN` فقط یک بار و در لحظهٔ ثبت نهایی تولید می‌شود
> (`engine/knowledge_numbering.py`، الگوی `<کد پروژه>-KN-<سال شمسی>-<سریال>`).
> برای project_id=None، کد عمومی «KN» استفاده می‌شود → `KN-KN-1405-001`.
> هیچ‌کدام از عملیات دانش hard-delete نیست.
>
> ستون‌های اختیاری فاز۳:
> - `pdf_path` / `docx_path` — مسیر فایل‌های خروجی PDF/Word
> - `interview_history_json` — تاریخچهٔ مکالمهٔ مصاحبه با AI
> - `tree_path_json` — مسیر انتخابی در درخت دانش رسمی (آرایه از نام نودها)
> - `org_metadata_json` — متادیتای سازمانی (committee/seed/colleagues/scope/hashtags_override)
> migration idempotent در `db/init.py` همه را به دیتابیس‌های قدیمی اضافه می‌کند.

### Knowledge rendering (فاز دانش سازمانی — `engine/knowledge_draft.py` + `engine/knowledge_render.py`)
- `build_report(*, knowledge_type, title, fields, hashtags, impact_type, project_name=None, contractor_name=None, reporter_name, reporter_title, reported_date, kn_number=None, raw_description=None, attachments=None, narrative_override=None, tree_path=None, org_metadata=None) -> dict`
  - مدل گزارش استاندارد هشت‌بخشی DANA (dana-draft.md §8).
  - `project_name`/`contractor_name` اختیاری (None → `[اختیاری - ارائه نشده]`).
  - `narrative_override`: اگر داده شود جایگزین شرح مکانیکی در بخش محتوا می‌شود.
  - `tree_path`: مسیر درخت دانش → نمایش در فراداده و حذف از unresolved.
  - `org_metadata`: کلیدهای committee/seed/colleagues/scope/hashtags_override.
- `render_text(report) -> str` — متن قابل‌کپی برای تلگرام از همین مدل.
- `render_dana_pdf(report, out_path) -> bool` — PDF با reportlab + فونت عربی روی سرور؛ اگر فونت پیدا نشود `False` برمی‌گرداند.
- `render_dana_docx(report, out_path) -> str` — Word با python-docx (فونت‌ها فقط اسم).

### Knowledge AI — استخراج، مصاحبه، polish (فاز۳ — `engine/knowledge_ai.py` + `engine/knowledge_interview.py`)
- `FIELD_SCHEMAS` — نقشهٔ کامل فیلدهای هر نوع (lesson:9، suggestion:7، explicit:4)
- `BUTTON_FIELDS` — فیلدهای دکمهای (`impact_type` در suggestion، `subtype` در explicit)
- `INTERVIEW_FRAMEWORKS` — ترتیب پرسش فیلدها برای مصاحبه
- `extract_fields(knowledge_type, raw_text)` — استخراج تک‌نوبت (legacy/روش دستی)
- `is_ai_enabled() -> bool`
- `interview_next_turn(knowledge_type, history, user_message) -> dict` — یک نوبت مصاحبه با AI (با retry)
- `polish_dana_draft(knowledge_type, fields, raw_description, project_name, contractor_name) -> dict` — ساخت narrative + hashtags + استخراج پروژه/پیمانکار
- `suggest_tree_paths(knowledge_type, fields, raw_description, title, top_k=3) -> list[dict]` — ۳ پیشنهاد مسیر درخت دانش

### Knowledge Tree (فاز۳ — `engine/knowledge_tree.py`)
درخت رسمی دانش سازمانی (۱۹۰ مسیر برگ، ۲۳۶ نود) از `references/knowledge-tree.md` اسکیل.
- `KNOWLEDGE_TREE` — دیکت تو در تو
- `get_children(path) -> list[str]`، `is_leaf(path) -> bool`
- `get_leaf_paths() -> list[list[str]]`، `find_path_by_leaf_name(name)`
- `render_path(path) -> str`، `validate_path(path) -> bool`
- `tree_as_yaml() -> str` — برای پرامپت AI

### state machine — ثبت دانش (handlers/knowledge.py)
```
KN_MODE_SELECT (0) — زیرمنو: دستی/مصاحبه (+ resume اگر draft ناتمام باشد)
KN_TYPE (1)
KN_REPORTER_NAME (2)
KN_REPORTER_TITLE (3)
KN_DESCRIPTION (4) — روش دستی
KN_FIELD_ANSWER (5) — پرسش فیلد ناقص (متن یا impact_type دکمهای)
KN_INTERVIEW_FRAMEWORK (6) — نمایش چارچوب راهنما
KN_INTERVIEW_LOOP (7) — حلقهٔ مصاحبه با AI
KN_FINAL_ASSEMBLE (8) — پاس polish → ساخت draft → preview
KN_ORG_META (9) — تنظیمات سازمانی (درخت/کمیته/بذر/همکاران/هشتگ/محدوده)
KN_TREE (10) — انتخاب درخت دانش (AI suggestion/drill-down/type/skip)
KN_PREVIEW (11) — سه کلید همزمان: ویرایش/ضمیمه/تأیید
KN_FIELD_EDIT (12) — ویرایش یک فیلد از preview
KN_PHOTOS (13) — عکس/مدرک
KN_DATE (14) — تاریخ ثبت
KN_FINISH (15) — ثبت نهایی + ساخت PDF/DOCX + ارسال
```
**نکته:** پروژه/پیمانکار از فاز۳ از فلو حذف شد — تجربه لزوماً به پروژه/پیمانکار خاصی وابسته نیست.

### callback_data — ثبت دانش (handlers/knowledge.py)
```
ورود/زیرمنو:
  kn:new
  kn_resume:yes | kn_resume:no
  kn_mode:manual | kn_mode:interview

نوع و گزارشدهنده:
  kn_type:<lesson|suggestion|explicit>
  kn_skip:title

مصاحبه:
  kn_interview:start
  kn_interview:done

فیلدها (روش دستی):
  kn_skip_field
  kn_impact:کیفی | kn_impact:کمی

تنظیمات سازمانی:
  kn_org:tree | kn_org:committee | kn_org:seed
  kn_org:colleagues | kn_org:hashtags | kn_org:scope
  kn_org:done | kn_org:skip

درخت دانش:
  kn_tree:ai
  kn_tree:ai:pick:<idx>
  kn_tree:nav | kn_tree:nav:<level>:<idx>
  kn_tree:nav:back | kn_tree:confirm
  kn_tree:type | kn_tree:skip

ویرایش:
  kn_edit:back | kn_edit:field:<key> | kn_edit:btn:<value>

پیوست و ثبت:
  kn_photos_start | kn_photos_done
  kn_today | kn_finish
```

### متغیرهای محیطی — AI ثبت دانش (`config.py`)
| متغیر | پیش‌فرض | شرح |
|---|---|---|
| `KNOWLEDGE_AI_BASE_URL` | `https://opencode.ai/zen/go/v1` | نقطه پایانی OpenAI-سازگار (OpenCode Go) |
| `KNOWLEDGE_AI_API_KEY` | `OPENCODE_API_KEY` | کلید API |
| `KNOWLEDGE_AI_MODEL` | خالی (= AI غیرفعال) | نام مدل؛ خالی = پرسش دستی همه فیلدها |
| `KNOWLEDGE_AI_TIMEOUT` | 60 | تایم‌اوت استخراج (ثانیه) |

وابستگی‌های جدید: `reportlab`, `arabic-reshaper`, `python-bidi`, `python-docx` (در `requirements.txt`).

---

## Public APIs — handlers/auth.py
LEVEL_PROJECT_MANAGER = 1
LEVEL_CONTRACTOR_MANAGER = 2
LEVEL_OPERATOR = 3
- `get_effective_level(telegram_id, project_id=None, contractor_id=None) -> int | None` — بدون context هرگز سطح ۲/۳ را تشخیص نمی‌دهد (طراحی عمدی؛ جزئیات در ARCHITECTURE.md)
- `can_manage_projects(telegram_id) -> bool`
- `can_manage_contractors(telegram_id, project_id) -> bool`
- `can_select_contractor(telegram_id, project_id, contractor_id) -> bool`
- `can_grant_level3(telegram_id, project_id) -> bool`
- `get_my_project_ids(telegram_id) -> list[int] | None` — `None` = سطح ۱ (بدون محدودیت)؛ `list` = سطح ۲/۳ (فقط این project_idها مجازند)
- `get_my_contractor_id_for_project(telegram_id, project_id) -> int | None` — `None` = سطح ۱ یا ۲ در این پروژه (همه‌ی پیمانکاران مجاز)؛ `int` = سطح ۳ (فقط این contractor_id مجاز)
- `get_role`, `is_admin`, `is_authenticated` — فقط از `get_effective_level` می‌خوانند؛ `access_grants` تنها منبع حقیقت برای تصمیمات دسترسی است. جدول `users` دیگر در مسیر تصمیم‌گیری دسترسی خوانده نمی‌شود (فقط برای ثبت خودکار ادمین و نمایش نام باقی مانده).

---

## Public APIs — handlers/keyboards.py

- `main_menu_keyboard(telegram_id)` — مستقیم `telegram_id` می‌گیرد و خودش سطح دسترسی را تشخیص می‌دهد (نه `role`)
- `projects_list_keyboard(projects: list[dict]) -> InlineKeyboardMarkup`
- `project_detail_keyboard(project: dict) -> InlineKeyboardMarkup`
- `management_submenu_keyboard(telegram_id: int) -> InlineKeyboardMarkup` — وابسته به `handlers.auth.can_manage_projects`؛ هنوز در جریان اصلی فعال نشده (نگاه کنید ARCHITECTURE.md بدهی فنی)

---

## Public APIs — engine/report_builder.py

### `build_wpq_excel(qualification_id: int) -> str`
فایل Excel رسمی WPQ را از روی رکورد qualification در دیتابیس می‌سازد.
فقط از `get_qualification_by_id` و `get_welder_by_id` می‌خواند.
خروجی: مسیر مطلق فایل ذخیره‌شده در `config.EXCEL_EXPORT_PATH`.

### کلیدهای `qualifications.extra_data` (وضعیت: ⏳ در انتظار Patch)
باید در `_build_qualification_payload` اضافه شوند:
- `filler_gtaw`: dict `{designation, f_no, sfa}` یا `None`
- `filler_smaw`: dict `{designation, f_no, sfa}` یا `None`
- `elec_gtaw`: dict `{current, polarity}` یا `None`
- `elec_smaw`: dict `{current, polarity}` یا `None`
- `shielding_gas`: str یا `None`
- `progression`: str (از qr_result engine گرفته می‌شود)

⚠️ فیلد «ضخامت نمونه Plate» (برای `specimen_type=PLATE`) در هیچ‌جای
دیتابیس ذخیره نمی‌شود — بدهی فنی باز، نیاز به بررسی منبع داده.

---

## Access Rules Summary

| عملیات | سطح ۱ | سطح ۲ |
|---|---|---|
| افزودن/الحاق مجدد پیمانکار | هر پروژه | فقط پروژه‌ی خودش |
| ویرایش `contractors.name` (سراسری) | ✅ | ❌ |
| ویرایش `label` (محلی) | ✅ | فقط لینک‌های پروژه‌ی خودش |
| خاتمه‌ی لینک | مستقیم | فقط درخواست (نیاز تأیید سطح ۱) |
| تأیید/رد درخواست خاتمه | ✅ | ❌ |

### اطلاع‌رسانی خاتمه‌ی همکاری
درخواست خاتمه‌ی سطح ۲ به همه‌ی `config.ADMIN_IDS` با پیام فوری تلگرام
(دکمه‌های تأیید/رد) ارسال می‌شود. نتیجه‌ی تصمیم سطح ۱ (تأیید یا رد + دلیل)
به `termination_requested_by` بازگردانده می‌شود. فرض: `chat_id == telegram_id`
برای چت خصوصی با ربات.

---

## Callback Data Registry

### Projects (`handlers/projects.py`)
| pattern | handler |
|---|---|
| `menu:projects` | `show_projects_menu` |
| `proj:<id>` | `project_detail_callback` |
| `proj_new` | `add_project_start` (entry point) |
| `proj_edit:<id>` | `edit_project_start` (entry point) |
| `proj_term:<id>` | `terminate_project_confirm` (entry point) |
| `proj_term_yes:<id>` | `terminate_project_execute` |
| `proj_reactivate:<id>` | `reactivate_project_callback` |

### Contractors (`handlers/contractors.py`)
| pattern | handler |
|---|---|
| `ctr_list:<project_id>` | `show_contractors_menu` |
| `ctr_detail:<link_id>` | `contractor_link_detail` |
| `ctr_add:<project_id>` | `add_contractor_start` (entry point) |
| `ctr_skip_label` | `add_contractor_label_skip` |
| `ctr_relink:<project_id>:<contractor_id>` | `relink_contractor_start` (entry point) |
| `ctr_label_edit:<link_id>` | `edit_label_start` (entry point) |
| `ctr_rename:<link_id>` | `rename_contractor_start` (entry point, فقط سطح ۱) |
| `ctr_term_ask:<link_id>` | `terminate_link_ask` |
| `ctr_term_yes:<link_id>` | `terminate_link_execute` |
| `ctr_approve:<link_id>` | `approve_termination` (فقط سطح ۱) |
| `ctr_reject_start:<link_id>` | `reject_termination_start` (entry point, فقط سطح ۱) |

### Knowledge (`handlers/knowledge.py`) — فاز دانش سازمانی
| pattern | handler |
|---|---|
| `kn:new` | `kn_start` (entry point, همه سطوح) |
| `kn_proj:<id>` | `kn_project_selected` |
| `kn_ctr:<id>` | `kn_contractor_selected` |
| `kn_type:<lesson\|suggestion\|explicit>` | `kn_type` |
| `kn_skip:title` | `kn_reporter_title_skip` |
| `kn_skip_field` | `kn_field_skip` (رد فیلد ناقص جاری) |
| `kn_photos_done` | `kn_photos_done` |
| `kn_today` | `kn_date_today` |
| `kn_finish` | `kn_finish` |

### متغیرهای محیطی AI (ثبت دانش)
| متغیر | پیش‌فرض | توضیح |
|---|---|---|
| `KNOWLEDGE_AI_BASE_URL` | `https://opencode.ai/zen/go/v1` | نقطهٔ پایانی OpenAI-سازگار |
| `KNOWLEDGE_AI_API_KEY` | مقدار `OPENCODE_API_KEY` | کلید API |
| `KNOWLEDGE_AI_MODEL` | *(خالی)* | نام مدل؛ خالی = AI غیرفعال (fallback دستی) |
| `KNOWLEDGE_AI_TIMEOUT` | `60` | حداکثر زمان انتظار پاسخ مدل (ثانیه) |

> اگر کلید یا مدل تنظیم نشود، استخراج با AI انجام نمی‌شود و ربات همهٔ فیلدها را
> دستی می‌پرسد — ربات بدون AI هم کامل کار می‌کند.

⚠️ هر callback_data جدید باید قبل از merge با این جدول و با
`test_registration.py` / `access_management.py` چک شود تا تداخل الگو
پیش نیاید.

---

## Internal APIs

توابع private فقط داخل همان فایل استفاده می‌شوند و نباید توسط سایر
ماژول‌ها فراخوانی شوند.

---

## Known Open Technical Debt

- کلیدهای `extra_data` و فیلد «ضخامت نمونه Plate» در خروجی Excel WPQ
  هنوز ناتمام‌اند (نگاه کنید بخش `engine/report_builder.py` بالا).
- کاربر سطح ۲ صفحه‌ی UI مستقلی برای «پروژه‌های من» ندارد — فقط از طریق
  فرآیند ثبت آزمون به‌طور غیرمستقیم محدود می‌شود، ولی نمی‌تواند مستقیماً
  پروژه/پیمانکار خودش را از منو مدیریت کند (مثلاً درخواست خاتمه‌ی
  همکاری پیمانکار).
- `management_submenu_keyboard` هنوز در جریان اصلی فعال نشده.

---

## Update Policy

در صورت اضافه‌شدن public API جدید، این فایل باید هم‌زمان به‌روزرسانی شود.
