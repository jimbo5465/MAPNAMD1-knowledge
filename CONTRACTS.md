# قراردادهای داخلی MAPNAMD1-knowledge

## هدف

این فایل قراردادهای داخلی پروژه را تعریف می‌کند — برای حفظ سازگاری بین ماژول‌ها و جلوگیری از تغییرات ناسازگار در APIهای داخلی. هر توسعه‌دهنده (انسان یا AI) قبل از ایجاد feature جدید باید این فایل را مطالعه کند.

---

## قوانین توسعه

1. هیچ feature نباید feature دیگری را خراب کند.
2. توابع public نباید بدون دلیل تغییر signature داشته باشند.
3. دسترسی مستقیم به دیتابیس خارج از `db/models.py` مجاز نیست.
4. هر feature جدید باید مستقل و قابل commit باشد.
5. هر تغییر در public API باید هم‌زمان در این فایل ثبت شود.
6. هر تغییر در ساختار دیتابیس باید migration خودکار در `db/init.py` داشته باشد (نه حذف دیتابیس).
7. پیام‌های کاربر باید فارسی باشد — متن‌های UI فارسی هستند.
8. هر عملیات AI باید با قفل `busy_lock` محافظت شود تا تداخل پیش نیاید.
9. **پورت موازی:** هر تغییر در کد یک پلتفرم باید بلافاصله در نسخهٔ دیگر (handlers/ ↔ bale_app/) اعمال شود. باگ‌هایی که فقط در یک نسخه فیکس شوند، ناهماهنگی می‌سازند.
10. **هیچ مسیری نباید به در بسته بخورد:** هر ConversationHandler باید در تمام stateها خروجی منطقی بدهد (valid transition, fallback مناسب, یا `ConversationHandler.END`).

---

## قراردادهای دیتابیس

### قاعدهٔ هویت دوپلتفرمی (مهم!)

توابع `db/models.py` که پارامتر `telegram_id` دارند، از این پس **شناسهٔ هر پلتفرم**
(تلگرام یا بله) را می‌پذیرند و داخلی با `get_user_by_platform_id` به رکورد کاربر
resolve می‌کنند: `add_observation`, `list_observations_by_user`, `search_observations`,
`find_pending_knowledge_by_user`, `add_knowledge_entry`, `update_user`,
`list_knowledge_by_user`, `search_knowledge_by_user`.

هرگز مستقیم با شناسهٔ خام پلتفرم روی جدول `observations.telegram_id` کوئری نزنید —
همیشه از توابع models استفاده کنید تا داده‌های لینک‌شده گم نشوند.

### `db/phone_utils.py`

| تابع | امضا | توضیح |
|---|---|---|
| `normalize_phone` | `(phone) -> str \| None` | کلید مشترک ۱۰ رقمی؛ همهٔ فرمت‌های `+98915...` / `98915...` / `0915...` / ارقام فارسی → `915...` ؛ نامعتبر → None |
| `only_digits` | `(raw) -> str` | تبدیل ارقام فارسی/عربی به لاتین و حذف بقیهٔ کاراکترها |

### `db/models.py` — لینک حساب‌های بله و تلگرام

| تابع | امضا | توضیح |
|---|---|---|
| `get_user_by_platform_id` | `(platform_id) -> dict \| None` | جستجو در **هر دو** ستون `telegram_id` و `bale_id` |
| `get_user_by_telegram_id` | `(telegram_id) -> dict \| None` | جستجوی مستقیم در `telegram_id` (مصرف داخلی) |
| `get_user_by_db_id` | `(db_id) -> dict \| None` | جستجو با شناسهٔ داخلی `users.id` |
| `find_linkable_user` | `(phone, personnel_code) -> dict \| None` | تطبیق همزمان `phone_norm` + کد پرسنلی (لایهٔ امنیتی) |
| `link_platform_account` | `(db_user_id, platform_id, platform) -> None` | اتصال شناسهٔ `bale`/`telegram` به رکورد موجود |
| `deactivate_duplicate_accounts` | `(platform_id, keep_db_id, future_owner_key=None) -> int` | ادغام رکوردهای تکراری قدیمی — دانش/مشاهدات منتقل، رکورد غیرفعال |
| `register_or_link_user` | `(*, platform, platform_id, full_name, phone=None, personnel_code=None, project_name=None, position=None) -> tuple[int, bool]` | در پایان ثبت‌نام: ثبت جدید یا لینک به حساب موجود. خروجی: `(user_db_id, linked?)` |
| `update_user` | `(platform_id, **fields) -> None` | به‌روزرسانی فیلدهای پروفایل (با allowlist ستون‌های مجاز) |

قواعد لینک:
- لینک **فقط** با تطبیق شمارهٔ نرمال‌شده + کد پرسنلی انجام می‌شود (هر دو اجباری).
- ثبت اولیه در بله: `telegram_id` = شناسهٔ بله (کلید مالکیت) و `bale_id` = شناسهٔ بله.
- رکورد تکراریِ ادغام‌شده: `is_active=0`، `bale_id=NULL`، `telegram_id=-<id>` (sentinel منفی).
- انتقال داده هنگام ادغام: `knowledge_entries.reported_by` و `observations.telegram_id`
  به کلید مالکیت نهایی (`future_owner_key`) منتقل می‌شوند.

### `db/models.py` — دانش

| تابع | امضا | توضیح |
|---|---|---|
| `add_knowledge_entry` | `(telegram_id, knowledge_type, reporter_name, raw_description=None, reporter_title=None, project_name=None) -> int` | ایجاد رکورد دانش جدید. خروجی: `knowledge_id` |
| `get_knowledge_entry_by_id` | `(knowledge_id) -> dict \| None` | جزییات دانش |
| `set_knowledge_fields` | `(knowledge_id, fields, draft_text=None) -> None` | ذخیره فیلدهای استخراج‌شده + پیش‌نویس |
| `submit_knowledge_entry` | `(knowledge_id, kn_number, pdf_path, docx_path) -> None` | ثبت نهایی (status → submitted) |
| `set_knowledge_inactive` | `(knowledge_id) -> None` | غیرفعال کردن (رد کردن) |
| `list_knowledge_entries` | `(active_only=True) -> list[dict]` | همهٔ دانش‌ها |
| `list_knowledge_by_user` | `(telegram_id, active_only=True) -> list[dict]` | دانش‌های کاربر (جدیدترین اول) |
| `search_knowledge_by_user` | `(telegram_id, keyword, limit=30) -> list[dict]` | جستجو در kn_number / fields_json / draft_text / raw_description |
| `add_knowledge_photo` | `(knowledge_id, path) -> int` | افزودن عکس به دانش |
| `list_knowledge_photos` | `(knowledge_id) -> list[dict]` | لیست عکس‌های دانش |
| `set_knowledge_interview_history` | `(knowledge_id, history) -> None` | ذخیره تاریخچهٔ مصاحبه |
| `get_knowledge_interview_history` | `(knowledge_id) -> list` | بازیابی تاریخچهٔ مصاحبه |
| `set_knowledge_tree_path` | `(knowledge_id, path) -> None` | ذخیره مسیر درخت دانش |
| `get_knowledge_tree_path` | `(knowledge_id) -> list[str]` | بازیابی مسیر درخت |
| `set_knowledge_org_metadata` | `(knowledge_id, org_data) -> None` | ذخیره متادیتای سازمانی (committee, seed, colleagues, hashtags) |
| `get_knowledge_org_metadata` | `(knowledge_id) -> dict` | بازیابی متادیتای سازمانی |
| `find_pending_knowledge_by_user` | `(telegram_id) -> dict \| None` | یافتن پیش‌نویس ناتمام کاربر |

### `db/models.py` — مشاهدات

| تابع | امضا |
|---|---|
| `add_observation` | `(telegram_id, content, project_name=None, tags=None, title=None, obs_date=None) -> int` |
| `update_observation` | `(obs_id, content=None, tags=None, title=None, obs_date=None) -> None` |
| `list_observations_by_user` | `(telegram_id, status=None) -> list[dict]` |
| `get_observation_by_id` | `(obs_id) -> dict \| None` |
| `search_observations` | `(telegram_id, keyword=None, hashtag=None, obs_date=None, limit=20) -> list[dict]` |
| `promote_observation` | `(obs_id, knowledge_id) -> None` |
| `archive_observation` | `(obs_id) -> None` |
| `add_observation_attachment` | `(observation_id, file_path, file_name=None, mime_type=None, file_size=None) -> int` |
| `list_observation_attachments` | `(observation_id) -> list[dict]` |
| `get_observation_attachment` | `(attachment_id) -> dict \| None` |
| `remove_observation_attachment` | `(attachment_id) -> None` |

### ساختار جدول `users`

| ستون | نوع | توضیح |
|---|---|---|
| `id` | INTEGER PK | شناسهٔ داخلی (ارجاع `knowledge_entries.reported_by`) |
| `telegram_id` | INTEGER NOT NULL UNIQUE | شناسه تلگرام؛ اگر کاربر اول‌بار در بله ثبت شود، شناسهٔ بله همینجا می‌نشیند (کلید مالکیت مشاهدات) |
| `bale_id` | INTEGER UNIQUE nullable | شناسهٔ بله در صورت لینک شدن حساب |
| `phone_norm` | TEXT indexed | شمارهٔ نرمال‌شدهٔ ۱۰ رقمی — کلید تطبیق بین پلتفرم‌ها |
| `full_name` / `phone` / `personnel_code` / `project_name` / `position` | TEXT | پروفایل |
| `is_active` | 0/1 | رکوردهای ادغام‌شده = 0 |

### ساختار جدول `observations`

| ستون | نوع | توضیح |
|---|---|---|
| `id` | INTEGER PK | شناسه |
| `telegram_id` | INTEGER | **کلید مالکیت** = مقدار ستون `telegram_id` رکورد کاربر (نه لزوماً شناسهٔ تلگرام) |
| `title` | TEXT | عنوان مشاهده |
| `content` | TEXT | متن مشاهده |
| `status` | TEXT | `raw` / `maturing` / `promoted` / `archived` |
| `promoted_to_kn_id` | INTEGER | ارجاع به دانش در صورت ارتقا |
| `project_name` | TEXT | نام پروژه |
| `tags` | TEXT | JSON آرایهٔ هشتگ‌ها |
| `obs_date` | TEXT | تاریخ مشاهده (میلادی `YYYY-MM-DD`) |
| `created_at` / `updated_at` | TEXT | زمان‌ها |

### ساختار جدول `observation_attachments`

| ستون | نوع | توضیح |
|---|---|---|
| `id` | INTEGER PK | شناسه |
| `observation_id` | INTEGER FK | ارجاع به مشاهده (ON DELETE CASCADE) |
| `file_path` | TEXT | مسیر فایل روی دیسک |
| `file_name` | TEXT | نام فایل |
| `mime_type` | TEXT | نوع MIME |
| `file_size` | INTEGER | اندازه به بایت |
| `uploaded_at` | TEXT | زمان آپلود |

---

## قراردادهای Handler

### States در `handlers/observations.py` و `bale_app/observations.py`

هر دو نسخه stateهای یکسانی دارند:

```
OBS_CONTENT = 0        # دریافت متن/ویس/عکس اولیه
OBS_CONFIRM_VOICE = 1  # تأیید/اصلاح متن ترنسکرایب‌شده
OBS_EDIT_CHOICE = 2    # انتخاب جایگزین/افزودن برای اصلاح
OBS_ATTACHMENTS = 3    # دریافت پیوست‌ها
OBS_EXTEND = 4         # افزودن مطلب به مشاهده موجود
OBS_TITLE = 5          # دریافت عنوان
OBS_TAGS = 6           # دریافت هشتگ‌ها (اختیاری)
OBS_DATE = 7           # دریافت تاریخ (اختیاری)
OBS_SEARCH = 8         # دریافت عبارت جستجو
```

### فلوی ثبت مشاهده

```
متن: OBS_CONTENT → OBS_TITLE → OBS_TAGS → OBS_DATE → OBS_ATTACHMENTS
ویس: OBS_CONTENT → OBS_CONFIRM_VOICE → OBS_EDIT_CHOICE → OBS_TITLE
افزودن: OBS_EXTEND → (متن/ویس → _append_obs_extend → الحاق به همون مشاهده)
```

### States در `handlers/knowledge.py` و `bale_app/knowledge.py`

```
KN_MODE_SELECT = 0        # زیرمنو: روش دستی / مصاحبه با AI
KN_TYPE = 1               # نوع دانش
KN_REPORTER_NAME = 2      # نام گزارش‌دهنده
KN_REPORTER_TITLE = 3     # سمت (اختیاری)
KN_DESCRIPTION = 4        # شرح آزاد تجربه (روش دستی)
KN_FIELD_ANSWER = 5       # پرسش فیلدهای ناقص (یکی یکی)
KN_INTERVIEW_FRAMEWORK = 6  # نمایش چارچوب راهنما (روش مصاحبه)
KN_INTERVIEW_LOOP = 7     # حلقهٔ مصاحبه با AI
KN_FINAL_ASSEMBLE = 8     # پاس AI polish نهایی (transient)
KN_ORG_META = 9           # تنظیمات سازمانی (درخت/کمیته/بذر/همکاران/هشتگ/محدوده)
KN_TREE = 10              # انتخاب درخت دانش (sub-flow)
KN_PREVIEW = 11           # پیشنمایش + ۳ کلید
KN_FIELD_EDIT = 12        # ویرایش یک فیلد از preview
KN_PHOTOS = 13            # عکس/مدرک
KN_DATE = 14              # تاریخ ثبت
KN_FIELD_DIRECT = 15      # پرسش مستقیم فیلدهای دانا (دستیِ خالصِ بدون AI)
KN_FINISH = 16            # ثبت نهایی + ساخت PDF/DOCX + ارسال
KN_TYPE_CONFIRM = 17      # تأیید نوع پس از پیشنهاد AI (تعارض طبقه‌بندی)
KN_ARCHIVE_LIST = 18      # بایگانی دانش — لیست صفحه‌بندی‌شده/نتایج جستجو
KN_SEARCH_INPUT = 19      # دریافت عبارت جستجو در دانش‌ها
```

### callback_data — مشاهدات

| الگو | هدف |
|---|---|
| `obs:new` | شروع ثبت مشاهده |
| `obs:list` | لیست مشاهدات |
| `obs:view:<id>` | جزئیات مشاهده |
| `obs:extend:<id>` | افزودن مطلب |
| `obs:promote:<id>` | ارتقا به دانش |
| `obs:archive:<id>` | بایگانی |
| `obs:search` | راهاندازی جستجو |
| `obs:search:mode` | انتخاب حالت جستجو (keyword/hashtag/date) |
| `obs:listpage:N` | صفحهٔ N لیست (منبع: همه یا نتایج جستجو) |
| `obs:confirm_voice` / `obs:edit_voice` | تأیید/اصلاح ویس |
| `obs:edit_replace` / `obs:edit_append` | جایگزین/افزودن متن اصلاح‌شده |
| `obs:add_photo:<id>` / `obs:add_file:<id>` | افزودن پیوست |
| `obs:skip` | رد کردن (هشتگ/تاریخ) |
| `menu:main` | بازگشت به منو |

### callback_data — دانش

| الگو | هدف | یادداشت |
|---|---|---|
| `kn:new` | شروع ثبت جدید | → `KN_MODE_SELECT` |
| `kn:mode:direct` | ثبت دستی | |
| `kn:mode:interview` | مصاحبه با AI | |
| `kn:type:<lesson\|suggestion\|explicit>` | انتخاب نوع دانش | |
| `kn:skip` | رد کردن مرحلهٔ اختیاری | |
| `kn:field:N` | ارسال پاسخ فیلد N | |
| `kn:flag:confirm` / `kn:flag:reject` | تأیید/رد QA flags | |
| `kn:org:done` | پایان تنظیمات سازمانی | |
| `kn:org:skip` | رد تنظیمات سازمانی | |
| `kn:tree:ai` | پیشنهاد AI برای درخت | |
| `kn:tree:drill` | انتخاب دستی از درخت | |
| `kn:tree:type` | تایپ مسیر کامل | (کد مرده — هرگز استفاده نمی‌شود) |
| `kn:tree:skip` | رد کردن درخت | |
| `kn:back` | بازگشت به مرحلهٔ قبل | |
| `kn:edit:<field>` | ویرایش فیلد از preview | |
| `kn:edit:done` | پایان ویرایش فیلدها | |
| `kn:preview` | بازگشت به preview از ویرایش | |
| `photo:done` | پایان افزودن عکس | |
| `kn:finish` | ثبت نهایی | |
| `kn:summary` | خلاصهٔ دانش بعد از ثبت | |
| `kn:archive` | بایگانی (از دانش ثبت‌شده) | |
| `kn:list` | لیست صفحه‌بندی‌شدهٔ دانش‌ها | |
| `kn:search` | جستجوی دانش | |
| `kn:view:<id>` | مشاهدهٔ جزئیات دانش | |
| `kn:archpage:N` | صفحهٔ N لیست بایگانی | |
| `kn:promote:<obs_id>` | ارتقا از مشاهده به دانش | |

### زیرمنوی بایگانی و جستجو

| الگو | هدف |
|---|---|
| `archive:open` | زیرمنو: بایگانی مشاهدات / بایگانی دانش |
| `archive:obs` | زیرمنوی مشاهدات → دکمه‌های `obs:list` و `obs:search` |
| `archive:kn` | زیرمنوی دانش → دکمه‌های `kn:list` و `kn:search` |

### callback_data — ثبت‌نام و پروفایل

| الگو | هدف |
|---|---|
| `reg:start` | شروع ثبت‌نام |
| `reg:edit` | **(کد مرده — دکمه ساخته می‌شود ولی استفاده نمی‌شود)** |
| `profile:edit` | شروع ویرایش پروفایل (وارد می‌شود ولی دکمه‌ای ندارد) |
| `profile:view` | نمایش پروفایل |

---

## قراردادهای Engine

### `engine/knowledge_ai.py`

| تابع | امضا |
|---|---|
| `is_ai_enabled` | `() -> bool` |
| `extract_fields` | `(knowledge_type, raw_text) -> dict` (async) |
| `field_labels` | `(knowledge_type) -> dict[str, str]` |
| `field_order` | `(knowledge_type) -> list[str]` |
| `build_system_prompt` | `(knowledge_type) -> str` |
| `get_ordered_field_keys` | `(knowledge_type) -> list[str]` |
| `order_fields_by_priority` | `(knowledge_type, keys) -> list[str]` |

### `engine/knowledge_interview.py`

| تابع | امضا |
|---|---|
| `interview_next_turn` | `(context_data: dict) -> dict` (async) |
| `polish_dana_draft` | `(report: dict) -> dict` (async) |
| `suggest_tree_paths` | `(knowledge_type, raw_text) -> list[str]` (async) |
| `qa_flags` | `(knowledge_type, fields) -> dict` (async) |
| `build_interview_system_prompt` | `(project_name=None) -> str` |
| `build_polish_system_prompt` | `() -> str` |
| `build_tree_suggestion_system_prompt` | `(knowledge_type) -> str` |
| `build_qa_system_prompt` | `() -> str` |

### `engine/knowledge_numbering.py`

| تابع | امضا |
|---|---|
| `generate_knowledge_number` | `(project_id=None) -> str` |
| `project_code` | `(project_id) -> str` |
| `max_serial_for_project` | `(project_id) -> int` |
| `jalali_year` | `() -> int` |

### `engine/knowledge_tree.py`

| تابع | امضا | توضیح |
|---|---|---|
| `get_children` | `(path) -> list[str]` | فرزندان یک نود |
| `is_leaf` | `(path) -> bool` | آیا برگ است؟ |
| `get_leaf_paths` | `() -> list[list[str]]` | همهٔ مسیرهای برگ |
| `find_path_by_leaf_name` | `(leaf_name) -> list[str] \| None` | جستجو با نام برگ |
| `render_path` | `(path) -> str` | نمایش مسیر با / |
| `all_paths_as_lines` | `() -> list[str]` | همهٔ مسیرها به‌صورت خطی |
| `tree_as_yaml` | `() -> str` | درخت به‌صورت YAML |
| `validate_path` | `(path) -> bool` | اعتبارسنجی مسیر |
| `total_leaf_count` | `() -> int` | تعداد برگ‌ها |
| `total_node_count` | `() -> int` | تعداد کل نودها |

### `engine/knowledge_draft.py`

| تابع | امضا |
|---|---|
| `build_report` | `(entry, fields) -> dict` |
| `extract_title_from_draft` | `(draft) -> str` |
| `entry_display_title` | `(entry) -> str` |
| `render_text` | `(report) -> str` |

### `engine/knowledge_render.py`

| تابع | امضا |
|---|---|
| `render_dana_pdf` | `(report, out_path) -> bool` |
| `render_dana_docx` | `(report, out_path) -> str` |

### قالب فیلدهای دانش

فیلدهای استخراج‌شده توسط AI در قالب dict با کلیدهای استاندارد:
`title`, `description`, `lesson`, `solution`, `tags`, `references`

---

## قراردادهای Utils

### `utils/busy_lock.py`

| تابع | امضا | توضیح |
|---|---|---|
| `set_busy` | `(telegram_id, ttl=90) -> None` | قفل کردن کاربر |
| `clear_busy` | `(telegram_id) -> None` | آزاد کردن |
| `is_busy` | `(telegram_id) -> bool` | بررسی |

### `utils/dates.py`

| تابع | امضا |
|---|---|
| `jalali_to_gregorian` | `(jalali_str) -> str` (خروجی `YYYY-MM-DD`) — فقط `YYYY/MM/DD` کامل |
| `gregorian_to_jalali` | `(gregorian_str) -> str` (خروجی `YYYY/MM/DD`) |
| `gregorian_to_jalali_display` | `(gregorian_str) -> str` (خروجی `۱۵ اسفند ۱۴۰۲`) |
| `validate_jalali_date_str` | `(value) -> (bool, str \| None)` |

---

## قراردادهای وب‌اپ (webapp/)

| Endpoint | متد | توضیح |
|---|---|---|
| `/api/auth` | POST | `{init_data, platform}` → توکن نشست + پروفایل؛ 403 اگر ثبت‌نام نکرده |
| `/api/me` | GET | پروفایل کاربر نشست |
| `/api/kn` | GET | لیست صفحه‌بندی‌شدهٔ دانش‌های خود کاربر |
| `/api/kn/search?q=` | GET | جستجوی دانش‌های خود کاربر |
| `/api/kn/{id}` | GET | جزئیات دانش (فقط مالک) |
| `/api/obs` | GET | لیست مشاهدات خود کاربر |
| `/api/obs/search?q=` | GET | جستجوی مشاهدات |
| `/api/obs/{id}` | GET | جزئیات مشاهده + متادیتای پیوست‌ها |
| `/api/file/obs-att/{id}` | GET | دانلود پیوست (فقط مالک مشاهده) |

قواعد:
- همه به‌جز auth نیاز به `Authorization: Bearer ***` دارند.
- توکن نشست: payload JSON base64url + امضای HMAC با `WEBAPP_SECRET` — TTL ۷ روز.
- اعتبارسنجی initData: HMAC-SHA256 با secret مشتق‌شده از توکن همان پلتفرم
  (هر دو ترتیب مستندشده پذیرفته می‌شود) + چک تازگی `auth_date` (حداکثر ۲ روز).
- فرانت‌اند هیچ SDK خارجی‌ای را sync لود نمی‌کند؛ SDK بله به‌صورت غیرمسدودکننده
  و با timeout لود می‌شود و در تلگرام initData از URL خوانده می‌شود.

---

## قراردادهای environment

| متغیر | توضیح |
|---|---|
| `BOT_TOKEN` | توکن ربات تلگرام از BotFather (اجباری برای نسخهٔ تلگرام) |
| `BALE_BOT_TOKEN` | توکن ربات بله (اجباری برای نسخهٔ بله) |
| `KNOWLEDGE_AI_API_KEY` | کلید OpenCode Go (یا `OPENCODE_GO_API_KEY`) |
| `KNOWLEDGE_AI_BASE_URL` | پیش‌فرض `https://opencode.ai/zen/go/v1` |
| `KNOWLEDGE_AI_MODEL` | پیش‌فرض `deepseek-v4-flash` |
| `GROQ_API_KEY` | کلید Groq برای STT |
| `GROQ_STT_MODEL` | پیش‌فرض `whisper-large-v3-turbo` |
| `KNOWLEDGE_AI_TIMEOUT` | پیش‌فرض `120` (ثانیه) |
| `WEBAPP_SECRET` | کلید امضای توکن نشست وب‌اپ (فایل `/etc/knowledgebot/webapp.env` روی سرور) |

---

## ورژن‌ها

| تاریخ | تغییر |
|---|---|
| 1403/05 | نسخهٔ اولیه — فورک از WelderBot با پاکسازی |
| 1403/05 | افزودن ثبت‌نام اجباری، مشاهده، جستجو، قفل AI |
| 1404/06 | پورت کامل روی بله (`bale_app/` + `main_bale.py`) — دو پلتفرم، یک دیتابیس |
| 1404/06 | **لینک حساب‌های بله/تلگرام**: نرمال‌سازی شماره (`phone_utils`)، ستون‌های `bale_id`/`phone_norm`، لینک خودکار با شماره+کد پرسنلی، ادغام رکوردهای تکراری قدیمی |
| 1404/06 | **بایگانی و جستجو**: ترکیب دکمه‌های منو، لیست صفحه‌بندی‌شدهٔ دانش‌ها، جستجوی متنی در دانش‌ها (`kn:list` / `kn:search` / `kn:view`) |
| 1404/06 | **مینی‌اپ وب**: `webapp/` (FastAPI + SPA)، دامنهٔ `web.mohsekarim8.ir` با CF Origin Cert، دکمهٔ `web_app` در منوی بله، پشتیبانی تلگرام |
| 1404/06 | باگ ۱ (جستجوی ماه کامل) + باگ ۲ (ویس در OBS_EXTEND) فیکس شدند |