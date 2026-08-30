# درخت فایل‌های پروژه MAPNAMD1-knowledge

```
MAPNAMD1-knowledge/
│
├── main.py                        # نقطهٔ ورود ربات تلگرام — ثبت handler ها و اجرا
├── main_bale.py                   # نقطهٔ ورود ربات بله — همان معماری با bale_app/
├── config.py                      # تنظیمات — خواندن متغیرهای محیطی
├── requirements.txt               # وابستگی‌ها (نسخه‌های دقیق)
│
├── README.md                      # راهنمای پروژه (فارسی)
├── ARCHITECTURE.md                # معماری و لایه‌بندی (فارسی)
├── CONTRACTS.md                   # قراردادهای داخلی و API (فارسی)
├── RUN.md                         # راهنمای اجرا و استقرار
├── TREE.md                        # همین فایل — درخت پروژه
├── AGENTS.md                      # راهنمای محیط برای دستیار کدنویسی
├── BOT_INTRODUCTION.md            # معرفی ربات برای کاربران
├── KNOWLEDGE_PHASE3_PLAN.md       # برنامهٔ فاز سوم دانش (تاریخی)
│
├── test_link_accounts.py          # تست لینک حساب‌های بله/تلگرام (۳۰ سنجه، دیتابیس موقت)
├── test_migration.py              # تست migration دیتابیس قدیمی → جدید
├── test_archive_db.py             # تست بایگانی و جستجوی دانش (۱۱ سنجه)
│
├── webapp/                        # مینی‌اپ وب (بله/تلگرام) + API
│   ├── __init__.py
│   ├── auth.py                    # اعتبارسنجی initData + توکن نشست HMAC
│   ├── main.py                    # FastAPI — endpoints دانش/مشاهدات/پروفایل/فایل
│   └── static/                    # فرانت‌اند vanilla JS (RTL، تم خودکار)
│       ├── index.html
│       ├── app.js
│       └── style.css
│
├── db/                            # لایهٔ دیتابیس مشترک دو پلتفرم (تنها لایهٔ مجاز SQL)
│   ├── __init__.py
│   ├── init.py                    # ساخت جداول + migration خودکار (bale_id / phone_norm)
│   ├── models.py                  # مدل‌ها و CRUD + لینک حساب‌ها (register_or_link_user)
│   └── phone_utils.py             # نرمال‌سازی شماره موبایل (+98/98/0/ارقام فارسی → کلید ۱۰ رقمی)
│
├── engine/                        # لایهٔ منطق کسب‌وکار (بدون وابستگی به پلتفرم پیام‌رسان)
│   ├── __init__.py
│   ├── knowledge_ai.py            # استخراج فیلدها با AI
│   ├── knowledge_interview.py     # مصاحبهٔ هوشمند + ساخت فرم نهایی
│   ├── knowledge_draft.py         # ساخت گزارش DANA
│   ├── knowledge_render.py        # خروجی PDF / Word
│   ├── knowledge_tree.py          # درخت دانش سازمانی
│   └── knowledge_numbering.py     # شماره‌گذاری دانش
│
├── handlers/                      # لایهٔ رابط تلگرام
│   ├── __init__.py
│   ├── registration.py            # ثبت‌نام و پروفایل (+ لینک خودکار حساب بله)
│   ├── observations.py            # مشاهدات میدانی + جستجو
│   ├── knowledge.py               # ثبت دانش (دستی + مصاحبه) + بایگانی دانش
│   ├── archive.py                 # زیرمنوی «بایگانی و جستجو» (standalone)
│   ├── auth.py                    # دکوراتور require_registration
│   ├── keyboards.py               # سازنده‌های keyboard
│   ├── jalali_calendar.py         # تقویم جلالی تعاملی (تلگرام)
│   └── prompt_cleanup.py          # حذف پرامپت قبلی ربات در هر گام گفتگو
│
├── bale_app/                      # لایهٔ رابط بله (پورت handlers/ روی فریم‌ورک سبک)
│   ├── __init__.py
│   ├── framework.py               # فریم‌ورک سفارشی بله (Update/Context/Dispatcher/ConversationHandler)
│   ├── registration.py            # ثبت‌نام و پروفایل (+ لینک خودکار حساب تلگرام)
│   ├── observations.py            # مشاهدات میدانی + جستجو
│   ├── knowledge.py               # ثبت دانش (دستی + مصاحبه) + بایگانی دانش
│   ├── archive.py                 # زیرمنوی «بایگانی و جستجو» (standalone)
│   ├── auth.py                    # دکوراتور require_registration
│   ├── keyboards.py               # سازنده‌های keyboard
│   └── jalali_calendar.py         # تقویم جلالی تعاملی (بله)
│
├── utils/                         # ابزارهای عمومی (مشترک بین دو پلتفرم)
│   ├── __init__.py
│   ├── busy_lock.py               # قفل «در حال پردازش» AI
│   ├── dates.py                   # تبدیل تاریخ جلالی/میلادی
│   ├── jalali_calendar_core.py    # هستهٔ محاسبات تقویم جلالی
│   ├── text_normalizer.py         # تطبیق متون با difflib (آستانه ۰٫۹۰)
│   └── validators.py              # اعتبارسنجی
│
├── scripts/
│   ├── backup_db.py               # بکاپ روزانه دیتابیس (cron 3:30)
│   ├── eval_normalizer.py         # ارزیابی normalizer با داده‌های نمونه
│   └── gen_intro_docx.py          # تولید سند معرفی
│
├── deploy/
│   └── nginx/
│       └── knowledge-miniapp.conf # کانفیگ nginx وب‌اپ (HTTPS :2083 + پراکسی /api/)
│
├── data/                          # [runtime] دیتابیس SQLite — مشترک بین هر دو ربات
│   └── knowledge.db
│
├── media/                         # [runtime] فایل‌های کاربر
│   ├── obs_attachments/           # پیوست‌های مشاهدات (هر مشاهده یک پوشه)
│   ├── kn_photos/                 # عکس‌های دانش
│   └── exports/                   # خروجی‌های PDF/Word
│
├── logs/                          # [runtime] لاگ‌ها
│   └── knowledgebot.log
│
└── .gitignore                     # فایل‌های نادیده‌گرفته‌شده در گیت
```

## پوشه‌های runtime (در گیت نیستند)

| پوشه | محتوا |
|---|---|
| `data/` | دیتابیس SQLite — منبع اصلی داده، **مشترک بین ربات بله و تلگرام** |
| `media/obs_attachments/<id>/` | پیوست‌های هر مشاهده (عکس/PDF/فایل) |
| `media/kn_photos/` | عکس‌های دانش |
| `media/exports/` | خروجی‌های PDF/Word |
| `logs/` | فایل‌های لاگ (دورهای) |

## نکتهٔ مهم: دو پلتفرم، یک دیتابیس

هر دو ورودی (`main.py` تلگرام و `main_bale.py` بله) از همان `db/` استفاده می‌کنند.
یک کاربر با تطبیق **شمارهٔ موبایل نرمال‌شده + کد پرسنلی** بین دو پلتفرم شناسایی می‌شود
(جزئیات در `CONTRACTS.md` بخش «لینک حساب‌های بله و تلگرام»).