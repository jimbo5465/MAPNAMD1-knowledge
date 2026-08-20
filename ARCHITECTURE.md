# معماری ربات دانش سازمانی MAPNAMD1-knowledge

## نمای کلی

```
کاربر تلگرام
      │
      ▼
Application (main.py)
      │
      ▼
    Handler
      │
      ├────────────────► Engine (AI)
      │                       │
      │                       ▼
      │                  Database (db/models.py)
      │
      └────────────────► Keyboard / Output
```

## لایه‌ها

### ۱. main.py — نقطه‌ی ورود

- راه‌اندازی logging
- مقداردهی اولیه‌ی دیتابیس (ساخت جداول + migration)
- ساخت Telegram Application
- ثبت همه‌ی handler ها (ConversationHandler + CommandHandler + CallbackQueryHandler)
- ثبت هندلر سراسری قفل «در حال پردازش» (busy guard)
- ثبت error handler سراسری
- شروع polling

**نباید** شامل SQL، منطق AI، تولید خروجی یا منطق کسب‌وکار باشد.

### ۲. handlers/ — لایه‌ی رابط کاربر

مدیریت ارتباط با کاربر از طریق تلگرام:
- دریافت پیام/ویس/عکس
- مدیریت Conversation (state machine)
- نمایش keyboard
- اعتبارسنجی اولیه‌ی ورودی
- فراخوانی engine یا db

| فایل | وظیفه |
|---|---|
| `registration.py` | ثبت‌نام کاربر (نام، شماره، کد پرسنلی، واحد)، پروفایل و ویرایش |
| `observations.py` | ثبت مشاهده (متن/ویس/عکس + عنوان + هشتگ + تاریخ + پیوست)، مرور، جستجو |
| `knowledge.py` | ثبت دانش (دستی + مصاحبه با AI + عکس)، ساخت پیش‌نویس DANA |
| `auth.py` | `require_registration` دکوراتور — اجباری بودن ثبت‌نام |
| `keyboards.py` | سازنده‌های InlineKeyboardMarkup |

**نکته:** handler ها مجاز به اجرای مستقیم SQL نیستند — فقط از طریق `db/models.py`.

### ۳. engine/ — لایه‌ی منطق کسب‌وکار

هیچ وابستگی به تلگرام ندارد — فقط ورودی/خروجی純 Python.

| فایل | وظیفه |
|---|---|
| `knowledge_ai.py` | استخراج فیلدهای ساختارمند از متن با AI (OpenCode Go / deepseek-v4-flash) |
| `knowledge_interview.py` | مصاحبه‌ی هوشمند (سؤال‌های متنی بر اساس نوع دانش)، ساخت فرم نهایی DANA، پیشنهاد مسیر درخت |
| `knowledge_draft.py` | ساخت گزارش DANA از فیلدهای استخراج‌شده |
| `knowledge_render.py` | خروجی PDF (reportlab) و Word (python-docx) با پشتیبانی فارسی |
| `knowledge_tree.py` | درخت دانش سازمانی — مسیرهای سلسله‌مراتبی |
| `knowledge_numbering.py` | شماره‌گذاری خودکار دانش |

### ۴. db/ — لایه‌ی دسترسی به داده

تنها لایه‌ای که مجاز به اجرای SQL است. تمام CRUD از طریق `db/models.py`.

| فایل | وظیفه |
|---|---|
| `init.py` | `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE` migration |
| `models.py` | توابع CRUD (کاربر، دانش، مشاهده، پیوست) + جستجو |

**جداول اصلی:**
- `users` — کاربران ثبت‌نام‌شده
- `knowledge_entries` — دانش‌های ثبت‌شده
- `knowledge_photos` — عکس‌های دانش
- `observations` — مشاهدات میدانی
- `observation_attachments` — پیوست‌های مشاهده (عکس/PDF/فایل)

### ۵. utils/ — ابزارهای عمومی

| فایل | وظیفه |
|---|---|
| `busy_lock.py` | قفل per-user «در حال پردازش» با انقضای خودکار (۹۰ ثانیه) |
| `dates.py` | تبدیل تاریخ جلالی ↔ میلادی، اعتبارسنجی، نمایش فارسی |
| `validators.py` | اعتبارسنجی ورودی |

## اصول معماری

1. **لایه‌بندی:** handler → engine → db (هر لایه فقط لایه‌ی پایین‌تر را صدا می‌زند)
2. **دسترسی به DB:** فقط از طریق `db/models.py` — هیچ handler ای مجاز به SQL مستقیم نیست
3. **قفل AI:** هنگام پردازش AI، کاربر پیام «⏳ در حال بررسی...» می‌گیرد — با `utils/busy_lock.py`
4. **ثبت‌نام اجباری:** کاربر قبل از هر کاری باید ثبت‌نام کند (دکوراتور `require_registration`)
5. **migration خودکار:** ستون‌های جدید با `ALTER TABLE` در `init_db()` اضافه می‌شوند
6. **ذخیره‌سازی عکس:** سایز متوسط (~۸۰۰px) برای صرفه‌جویی در فضا
7. **systemd:** سرویس با `Restart=always` و `MemoryMax`/`CPUQuota` — خروج تمیز