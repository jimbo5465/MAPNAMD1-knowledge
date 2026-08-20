# راهنمای اجرا و استقرار MAPNAMD1-knowledge

## ۱. پیش‌نیازها

- Python 3.10 یا بالاتر
- دسترسی به اینترنت برای ارتباط با Telegram API + Groq API + OpenCode Go API
- توکن ربات از [@BotFather](https://t.me/BotFather)
- کلید Groq (برای STT — تبدیل ویس به متن)
- کلید OpenCode Go (برای هوش مصنوعی)

---

## ۲. نصب محیط مجازی

```bash
# ایجاد محیط مجازی
python -m venv .venv

# فعال‌سازی (Linux/Mac)
source .venv/bin/activate

# نصب وابستگی‌ها
pip install -r requirements.txt
```

---

## ۳. تنظیم متغیرهای محیطی

```bash
# توکن ربات از BotFather (اجباری)
export BOT_TOKEN="1234567890:ABCDefghIJKLMNOpqrsTUVwxyz"

# کلید OpenCode Go (برای AI)
export KNOWLEDGE_AI_API_KEY="sk-..."
export KNOWLEDGE_AI_MODEL="deepseek-v4-flash"

# کلید Groq (برای STT)
export GROQ_API_KEY="gsk_..."
export GROQ_STT_MODEL="whisper-large-v3-turbo"
```

> **نکته:** برای یافتن شناسه تلگرام خود، به [@userinfobot](https://t.me/userinfobot) پیام بدهید.

---

## ۴. اجرای محلی برای تست

```bash
python main.py
```

خروجی مورد انتظار:
```
2026-08-20 14:00:53 | __main__ | INFO | ============================================================
2026-08-20 14:00:53 | __main__ | INFO | در حال راه‌اندازی ربات MAPNAMD1-knowledge ...
2026-08-20 14:00:53 | __main__ | INFO | مسیر DB: /root/MAPNAMD1-knowledge/data/knowledge.db
2026-08-20 14:00:53 | __main__ | INFO | ✅ پایگاه داده آماده است.
2026-08-20 14:00:53 | __main__ | INFO | 🚀 MAPNAMD1-knowledge در حال اجرا است. منتظر پیام‌ها...
```

برای توقف: `Ctrl+C`

---

## ۵. خطاهای رایج

| خطا | معنی | راه‌حل |
|---|---|---|
| `BOT_TOKEN not set` | توکن تنظیم نشده | `export BOT_TOKEN='...'` |
| `ModuleNotFoundError` | وابستگی نصب نشده | `pip install -r requirements.txt` |
| `no such column: title` | دیتابیس قدیمی | حذف `data/knowledge.db` و اجرای مجدد — یا migration خودکار |
| `HTTP 403 error code: 1010` | IP بلاک شده | کلاینت را با httpx امتحان کنید (urllib فرستنده نیست) |
| `'NoneType' object has no attribute 'reply_text'` | خطا در callback query | `update.effective_message` را جایگزین `update.message` کنید |

---

## ۶. استقرار روی VPS با systemd

```bash
# ۱. کپی پروژه
sudo cp -r /root/MAPNAMD1-knowledge /opt/MAPNAMD1-knowledge

# ۲. ساخت venv روی سرور
cd /opt/MAPNAMD1-knowledge
python -m venv .venv
.venv/bin/pip install -r requirements.txt

# ۳. سرویس systemd
sudo cp /etc/systemd/system/knowledgebot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable knowledgebot
sudo systemctl start knowledgebot

# ۴. بررسی وضعیت
sudo systemctl status knowledgebot

# ۵. لاگ زنده
sudo journalctl -u knowledgebot -f
```

---

## ۷. ساختار فایل‌ها

```
MAPNAMD1-knowledge/
├── main.py              ← نقطه ورود (این را اجرا کن)
├── config.py            ← تنظیمات (از env vars می‌خواند)
├── requirements.txt     ← وابستگی‌ها
├── db/                  ← لایه پایگاه داده
├── engine/              ← موتور هوش مصنوعی و منطق دانش
├── handlers/            ← هندلرهای تلگرام
├── utils/               ← ابزارهای کمکی
├── data/                ← [runtime] فایل SQLite
├── media/               ← [runtime] عکس‌ها و فایل‌ها
└── logs/                ← [runtime] فایل‌های لاگ
```