# ARCHITECTURE.md

# WelderBot Architecture

WelderBot یک ربات تلگرام برای مدیریت تست صلاحیت جوشکاران (WQT) مطابق
ASME Section IX است، با معماری ماژولار طراحی‌شده برای توسعه‌ی مستقل هر Feature.

---

## Architecture Overview
Telegram User
│
▼
Application (main.py)
│
▼
Handlers
│
├──────────────► Engine
│                  │
│                  ▼
│                 Database
│
└──────────────► Forms
---

## Module Responsibilities

### main.py
راه‌اندازی logging، مقداردهی اولیه دیتابیس، ساخت Telegram Application،
ثبت همه‌ی handlerها، شروع polling.
نباید شامل SQL، منطق ASME، تولید Excel یا منطق کسب‌وکار باشد.

### handlers/
مدیریت ارتباط با کاربر از طریق تلگرام: دریافت پیام، مدیریت conversation،
نمایش keyboard، اعتبارسنجی اولیه‌ی ورودی، فراخوانی engine یا db.
نباید شامل SQL یا محاسبات ASME باشد.

### engine/
پیاده‌سازی قوانین ASME Section IX — محاسبات qualification، تعیین محدوده‌ی
صلاحیت. وابستگی به تلگرام ندارد.

شامل `report_builder.py`: ماژول مستقلی که فقط از `db.models` می‌خواند و
هیچ وابستگی به context مکالمه‌ی تلگرام ندارد (طبق قانون لایه‌بندی
`db ← engine`). ورودی: `qualification_id`. خروجی: مسیر فایل Excel
تولیدشده. Template مرجع: `media/templates/WPQ_template.xlsx`
(فرم اصلاح‌شده با split شدن سلول‌های label/value).

### db/
تنها لایه‌ی دسترسی به پایگاه داده. تمام CRUD فقط اینجا انجام می‌شود؛
هیچ handler‌ای مجاز به اجرای مستقیم SQL نیست.

### forms/
تولید فایل‌های Excel — فقط خروجی تولید می‌کند.

### utils/
توابع عمومی مورد استفاده در کل پروژه.

### config.py
مدیریت تنظیمات؛ تمام متغیرهای محیطی از این فایل خوانده می‌شوند.

---

## Startup Sequence
1. راه‌اندازی logging
2. بررسی تنظیمات
3. مقداردهی اولیه دیتابیس
4. ساخت Telegram Application
5. ثبت همه‌ی handlerها
6. شروع polling

## Handler Registration Order
1. Error Handler
2. Conversation Handlers
3. Command Handlers
4. CallbackQuery Handlers
5. Fallback Handlers

---

## Dependency Rules
main.py
↓
handlers
↓
engine
↓
db
forms
↓
db
utils ← قابل استفاده در همه‌ی ماژول‌ها
## Development Rule
هر قابلیت جدید باید یک handler مستقل باشد. برای اضافه‌شدن یک feature
جدید فقط ثبت handler جدید در main.py مجاز است؛ ساختار معماری نباید
تغییر کند.

---

## Current Features
- Authentication
- Main Menu
- Welder Management / WQT Registration (ASME Section IX)
- Excel Export (WPQ)
- Access Management (سطوح ۱/۲/۳)
- Project Management
- Contractor Management (با چرخه‌حیات رابطه‌ی پروژه⇆پیمانکار)

---

## سیستم دسترسی (Access Control)

### جداول
- `access_grants` — هر ردیف یک دسترسی: `(telegram_id, level, project_id, contractor_id)`.
  سطح ۱: هر دو NULL. سطح ۲: فقط project_id. سطح ۳: هر دو پر.
- `pending_users` — هر کسی که `/start` زده، صرف‌نظر از داشتن دسترسی.
- `project_contractors` — رابطه‌ی چند‌به‌چند پروژه⇆پیمانکار (جایگزین ستون
  حذف‌شده‌ی `projects.contractor_id`).

### منطق مرکزی: `handlers/auth.py`
توابع تصمیم‌گیری: `get_effective_level`, `can_manage_projects`,
`can_manage_contractors`, `can_select_contractor`, `can_grant_level3`,
`get_my_project_ids`, `get_my_contractor_id_for_project`.
همه‌ی handlerها باید فقط از این توابع استفاده کنند، نه SQL مستقیم.

**رفتار حیاتی `get_effective_level`:** بدون context (بدون `project_id`/
`contractor_id`) هرگز سطح ۲ یا ۳ را تشخیص نمی‌دهد و همیشه `None`
برمی‌گرداند — این طراحی عمدی است چون سطح ۲/۳ ذاتاً scoped هستند. هر کد
جدیدی که می‌پرسد «آیا این کاربر اصلاً سطح X هست، در هر پروژه‌ای؟» باید
مستقیم `access_grants` را بخواند، نه `get_effective_level` بدون آرگومان
را صدا بزند (وگرنه خطای بی‌صدا/دکمه‌ی نامرئی تولید می‌شود).

**الگوی enforcement:** `get_my_project_ids` و
`get_my_contractor_id_for_project` هر دو یک قرارداد دارند:
`None` = بدون محدودیت (سطح بالاتر)، مقدار مشخص = محدودیت. فیچرهای
جدیدِ کنترل‌دسترسی باید از همین الگو پیروی کنند.

### یک منبع حقیقت
config.ADMIN_IDS ──┐
├──> get_effective_level() ──> get_role()/is_admin()/is_authenticated()
access_grants ──────┘         │
└──> can_manage_projects()/can_manage_contractors()/
get_my_project_ids()/get_my_contractor_id_for_project()
جدول `users` فقط برای ثبت خودکار ادمین و نمایش نام باقی مانده — در مسیر
تصمیم‌گیری دسترسی نیست. بازطراحی مفهوم پایه (سطح دسترسی) فقط از طریق
بازنویسی تابع مرکزی (`get_role`) انجام می‌شود، بدون نیاز به تغییر همه‌ی
محل‌های صدازننده، تا وقتی امضا و قرارداد خروجی ثابت بماند.

### فایل `handlers/access_management.py`
ConversationHandler مستقل برای اعطای دسترسی — الگوی state machine ساده
(بدون پشته‌ی back/cancel)، شبیه `welders.py`، نه الگوی پیچیده‌ی
`test_registration.py`.

---

## مدیریت پروژه و پیمانکار

`handlers/projects.py` و `handlers/contractors.py` از الگوی
`handlers/welders.py` + `handlers/keyboards.py` پیروی می‌کنند و برای
گیت‌کردن دسترسی از `can_manage_projects` / `can_manage_contractors`
استفاده می‌کنند.

### تفکیک «پیمانکار» از «رابطه‌ی پروژه⇆پیمانکار»
پیمانکار (`contractors`) یک entity سراسری با نام یکتا در کل سیستم است.
وضعیت فعال/خاتمه‌یافته و برچسب نمایشی («الحاقیه»، «فاز ۲») به **رابطه**
(`project_contractors`) تعلق دارد، نه به خود پیمانکار — یک پیمانکار
می‌تواند هم‌زمان در چند پروژه با وضعیت‌های متفاوت فعال باشد؛ خاتمه‌ی
همکاری در یک پروژه روی پروژه‌های دیگر همان پیمانکار اثر نمی‌گذارد.
`project_contractors` بنابراین یک چرخه‌حیات کامل دارد:
فعال → در انتظار خاتمه → خاتمه‌یافته → (الحاق مجدد: رکورد جدید).

### الگوی دومرحله‌ای درخواست/تأیید
خاتمه‌ی همکاری توسط سطح ۲ بلافاصله اجرا نمی‌شود — درخواست ثبت می‌شود،
مدیر سراسری (سطح ۱) با پیام فوری تلگرام مطلع می‌شود و باید تأیید یا رد
کند (`pending_termination` state + notify + approve/reject). فیچرهای
آینده که به تأیید بالادست نیاز دارند (مثلاً حذف جوشکار توسط سطح ۲) باید
از همین ساختار (`request_*` / `approve_*` / `reject_*` + notify) پیروی کنند.

### استقلال از ماژول جوشکاری
`handlers/contractors.py` و توابع مرتبط در `db/models.py` فقط به
`projects`، `contractors`، `project_contractors` وابسته‌اند — هیچ ارجاعی
به `welders` یا `qualifications` ندارند (برای توسعه‌پذیری به رشته‌های
آینده: رنگ، مونتاژ، فیتینگ). تابع `is_link_active(project_id,
contractor_id)` API آماده برای گیت‌کردن «ثبت فعالیت جدید» در هر ماژول
فعالیتی آینده است.

---

## بدهی فنی باز
- توابع کمکی UI (`_kb`, `_render`, `_nav_row`) هنوز داخل
  `handlers/test_registration.py` تعریف شده‌اند؛ باید به فایل مستقل
  `utils/telegram_ui.py` منتقل شوند تا فیچرهای جدید فقط به این فایل
  مشترک وابسته باشند، نه به فایل‌های handler دیگر.
- گیت «فقط مشاهده/جستجو، بدون ثبت فعالیت جدید» برای پیمانکار
  `pending_termination`/`terminated` هنوز در `handlers/test_registration.py`
  اعمال نشده — باید با فراخوانی `is_link_active` اضافه شود.
- `management_submenu_keyboard` هنوز فعال نشده — nesting منوی «⚙️ مدیریت»
  (پروژه + پیمانکار + کاربران زیر یک دکمه) به بعد از تکمیل «مدیریت
  کاربران» موکول شده.
  
