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

---

## قراردادهای دیتابیس

### قاعدهٔ هویت دوپلتفرمی (مهم!)

توابع `db/models.py` که پارامتر `telegram_id` دارند، از این پس **شناسهٔ هر پلتفرم**
(تلگرام یا بله) را می‌پذیرند و داخلی با `get_user_by_platform_id` به رکورد کاربر
resolve می‌کنند: `add_observation`, `list_observations_by_user`, `search_observations`,
`find_pending_knowledge_by_user`, `add_knowledge_entry`, `update_user`.

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
| `find_linkable_user` | `(phone, personnel_code) -> dict \| None` | تطبیق همزمان `phone_norm` + کد پرسنلی (لایهٔ امنیتی) |
| `link_platform_account` | `(db_user_id, platform_id, platform) -> None` | اتصال شناسهٔ `bale`/`telegram` به رکورد موجود |
| `deactivate_duplicate_accounts` | `(platform_id, keep_db_id, future_owner_key=None) -> int` | ادغام رکوردهای تکراری قدیمی — دانش/مشاهدات منتقل، رکورد غیرفعال |
| `register_or_link_user` | `(*, platform, platform_id, full_name, phone=None, personnel_code=None, project_name=None, position=None) -> tuple[int, bool]` | در پایان ثبت‌نام: ثبت جدید یا لینک به حساب موجود. خروجی: `(user_db_id, linked?)` |

قواعد لینک:
- لینک **فقط** با تطبیق شمارهٔ نرمال‌شده + کد پرسنلی انجام می‌شود (هر دو اجباری).
- ثبت اولیه در بله: `telegram_id` = شناسهٔ بله (کلید مالکیت) و `bale_id` = شناسهٔ بله.
- رکورد تکراریِ ادغام‌شده: `is_active=0` ، `bale_id=NULL` ، `telegram_id=-<id>` (sentinel منفی).
- انتقال داده هنگام ادغام: `knowledge_entries.reported_by` و `observations.telegram_id`
  به کلید مالکیت نهایی (`future_owner_key`) منتقل می‌شوند.

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
| `tags` | TEXT | JSON آرایه‌ی هشتگ‌ها |
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

### States در `handlers/observations.py`

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
OBS_CONTENT → OBS_TITLE → OBS_TAGS → OBS_DATE → OBS_ATTACHMENTS
ویس: OBS_CONTENT → OBS_CONFIRM_VOICE → OBS_EDIT_CHOICE → OBS_TITLE
```

### callback_data ها

| الگو | هدف |
|---|---|
| `obs:new` | شروع ثبت مشاهده |
| `obs:list` | لیست مشاهدات |
| `obs:view:<id>` | جزئیات مشاهده |
| `obs:extend:<id>` | افزودن مطلب |
| `obs:promote:<id>` | ارتقا به دانش |
| `obs:archive:<id>` | بایگانی |
| `obs:search*` | جستجو |
| `obs:edit_voice` / `obs:confirm_voice` | تأیید/اصلاح ویس |
| `obs:edit_replace` / `obs:edit_append` | جایگزین/افزودن متن |
| `obs:add_photo:<id>` / `obs:add_file:<id>` | افزودن پیوست |
| `menu:main` | بازگشت به منو |

---

## قراردادهای Engine

### `engine/knowledge_ai.py`

| تابع | امضا |
|---|---|
| `is_ai_enabled` | `() -> bool` |
| `extract_fields` | `(knowledge_type, raw_text) -> dict` (async) |
| `field_labels` | `(knowledge_type) -> dict[str, str]` |
| `field_order` | `(knowledge_type) -> list[str]` |

### `engine/knowledge_interview.py`

| تابع | امضا |
|---|---|
| `interview_next_turn` | `(context_data: dict) -> dict` (async) |
| `polish_dana_draft` | `(report: dict) -> dict` (async) |
| `suggest_tree_paths` | `(knowledge_type, raw_text) -> list[str]` (async) |

### `engine/knowledge_numbering.py`

| تابع | امضا |
|---|---|
| `generate_knowledge_number` | `(project_id=None) -> str` |

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
| `jalali_to_gregorian` | `(jalali_str) -> str` (خروجی `YYYY-MM-DD`) |
| `gregorian_to_jalali` | `(gregorian_str) -> str` (خروجی `YYYY/MM/DD`) |
| `gregorian_to_jalali_display` | `(gregorian_str) -> str` (خروجی `۱۵ اسفند ۱۴۰۲`) |
| `validate_jalali_date_str` | `(value) -> (bool, str \| None)` |

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

---

## ورژن‌ها

| تاریخ | تغییر |
|---|---|
| 1403/05 | نسخه‌ی اولیه — فورک از WelderBot با پاکسازی |
| 1403/05 | افزودن ثبت‌نام اجباری، مشاهده، جستجو، قفل AI |
| 1404/06 | پورت کامل روی بله (`bale_app/` + `main_bale.py`) — دو پلتفرم، یک دیتابیس |
| 1404/06 | **لینک حساب‌های بله/تلگرام**: نرمال‌سازی شماره (`phone_utils`)، ستون‌های `bale_id`/`phone_norm`، لینک خودکار با شماره+کد پرسنلی، ادغام رکوردهای تکراری قدیمی |
