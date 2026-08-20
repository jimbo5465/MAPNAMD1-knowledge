# راهنمای اجرا و استقرار WelderBot

## ۱. پیش‌نیازها

- Python 3.11 یا بالاتر
- دسترسی به اینترنت برای ارتباط با Telegram API
- توکن ربات از [@BotFather](https://t.me/BotFather)

---

## ۲. نصب محیط مجازی

```bash
# ایجاد محیط مجازی
python -m venv .venv

# فعال‌سازی (Linux/Mac)
source .venv/bin/activate

# فعال‌سازی (Windows)
.venv\Scripts\activate

# نصب وابستگی‌ها
pip install -r requirements.txt
```

---

## ۳. تنظیم متغیرهای محیطی

```bash
# توکن ربات از BotFather
export BOT_TOKEN="1234567890:ABCDefghIJKLMNOpqrsTUVwxyz"

# شناسه‌های عددی تلگرام ادمین‌ها (با کاما جدا)
export ADMIN_IDS="123456789,987654321"

# فعال‌سازی حالت debug (اختیاری)
export WELDERBOT_DEBUG=1
```

> **نکته:** برای یافتن شناسه تلگرام خود، به [@userinfobot](https://t.me/userinfobot) پیام بدهید.

---

## ۴. اجرای محلی برای تست

```bash
python main.py
```

خروجی مورد انتظار (بنر فارسی):
```
2024-03-10 10:00:00 | __main__        | INFO     | ============================================================
2024-03-10 10:00:00 | __main__        | INFO     | در حال راه‌اندازی ربات WelderBot ...
2024-03-10 10:00:00 | __main__        | INFO     | مسیر DB: /path/to/data/welderbot.db
2024-03-10 10:00:00 | __main__        | INFO     | ✅ پایگاه داده آماده است.
2024-03-10 10:00:00 | __main__        | INFO     | 🚀 WelderBot در حال اجرا است. منتظر پیام‌ها...
```

برای توقف: `Ctrl+C`

---

## ۵. اگر خطا گرفتی

| خطا | معنی | راه‌حل |
|-----|------|---------|
| `ImportError: cannot import name 'X'` | نام تابع یا فایل با قرارداد نمی‌خواند | فایل مربوطه را بررسی کن؛ نام باید با CONTRACTS.md یکی باشد |
| `ModuleNotFoundError: No module named 'X'` | پکیج نصب نشده | `pip install -r requirements.txt` را دوباره اجرا کن |
| `sqlite3.OperationalError: no such column` | schema با DATA_SCHEMA.md یکی نیست | `data/welderbot.db` را حذف کن و مجدد اجرا کن |
| `BOT_TOKEN error` یا `PLACEHOLDER_BOT_TOKEN` | متغیر محیطی ست نشده | `export BOT_TOKEN='...'` را بزن |
| `sqlite3.OperationalError: no such table` | جداول ساخته نشده‌اند | `python -c "from db.init import init_db; init_db()"` را بزن |
| `Telegram error: Unauthorized` | توکن اشتباه است | توکن را از BotFather دوباره بگیر |

> اگر مشکل حل نشد: traceback کامل را کپی کن و بفرست.

---

## ۶. استقرار روی VPS با systemd

```bash
# ۱. کپی پروژه
sudo cp -r . /opt/welderbot/

# ۲. ساخت venv روی سرور
cd /opt/welderbot
python -m venv .venv
.venv/bin/pip install -r requirements.txt

# ۳. ویرایش متغیرهای محیطی در فایل service
sudo nano /opt/welderbot/welderbot.service
# BOT_TOKEN و ADMIN_IDS را با مقادیر واقعی جایگزین کن

# ۴. نصب و فعال‌سازی سرویس
sudo cp welderbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable welderbot
sudo systemctl start welderbot

# ۵. بررسی وضعیت
sudo systemctl status welderbot

# ۶. مشاهده لاگ زنده
sudo journalctl -u welderbot -f

# ۷. مشاهده لاگ فایل (اگر logs/ وجود دارد)
tail -f /opt/welderbot/logs/welderbot.log
```

---

## ۷. ساختار فایل‌ها

```
welderbot/
├── main.py              ← نقطه ورود (این را اجرا کن)
├── config.py            ← تنظیمات (از env vars می‌خواند)
├── requirements.txt     ← وابستگی‌ها
├── welderbot.service    ← یونیت systemd
├── db/                  ← لایه پایگاه داده
├── engine/              ← موتور محاسبه ASME
├── handlers/            ← هندلرهای تلگرام
├── forms/               ← تولید فایل Excel
├── utils/               ← ابزارهای کمکی
├── data/                ← [runtime] فایل SQLite
├── media/               ← [runtime] عکس‌ها و فایل‌های Excel
└── logs/                ← [runtime] فایل‌های لاگ
```
