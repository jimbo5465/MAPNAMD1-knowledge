# درخت فایل‌های پروژه MAPNAMD1-knowledge

```
MAPNAMD1-knowledge/
│
├── main.py                        # نقطه‌ی ورود ربات — ثبت handler ها و اجرا
├── config.py                      # تنظیمات — خواندن متغیرهای محیطی
├── requirements.txt               # وابستگی‌ها (نسخه‌های دقیق)
├── README.md                      # راهنمای پروژه (فارسی)
├── ARCHITECTURE.md                # معماری و لایه‌بندی (فارسی)
├── CONTRACTS.md                   # قراردادهای داخلی و API (فارسی)
├── RUN.md                         # راهنمای اجرا و استقرار
├── KNOWLEDGE_PHASE3_PLAN.md       # برنامه‌ی فاز سوم دانش
│
├── db/                            # لایه‌ی دیتابیس (تنها لایه‌ی مجاز SQL)
│   ├── __init__.py
│   ├── init.py                    # ساخت جداول + migration خودکار
│   └── models.py                  # مدل‌ها و توابع CRUD
│
├── engine/                        # لایه‌ی منطق کسب‌وکار (بدون وابستگی تلگرام)
│   ├── __init__.py
│   ├── knowledge_ai.py            # استخراج فیلدها با AI
│   ├── knowledge_interview.py     # مصاحبه‌ی هوشمند + ساخت فرم نهایی
│   ├── knowledge_draft.py         # ساخت گزارش DANA
│   ├── knowledge_render.py        # خروجی PDF / Word
│   ├── knowledge_tree.py          # درخت دانش سازمانی
│   └── knowledge_numbering.py     # شماره‌گذاری دانش
│
├── handlers/                      # لایه‌ی رابط تلگرام
│   ├── __init__.py
│   ├── registration.py            # ثبت‌نام و پروفایل
│   ├── observations.py            # مشاهدات میدانی + جستجو
│   ├── knowledge.py               # ثبت دانش (دستی + مصاحبه)
│   ├── auth.py                    # دکوراتور require_registration
│   └── keyboards.py               # سازنده‌های keyboard
│
├── utils/                         # ابزارهای عمومی
│   ├── __init__.py
│   ├── busy_lock.py               # قفل «در حال پردازش» AI
│   ├── dates.py                   # تبدیل تاریخ جلالی/میلادی
│   └── validators.py              # اعتبارسنجی
│
├── data/                          # [runtime] دیتابیس SQLite
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
| `data/` | دیتابیس SQLite — منبع اصلی داده |
| `media/obs_attachments/<id>/` | پیوست‌های هر مشاهده (عکس/PDF/فایل) |
| `media/kn_photos/` | عکس‌های دانش |
| `media/exports/` | خروجی‌های PDF/Word |
| `logs/` | فایل‌های لاگ |
