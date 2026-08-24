# راهنمای اجرا و استقرار MAPNAMD1-knowledge

## ۱. پیش‌نیازها

- Python 3.10 یا بالاتر
- دسترسی به اینترنت برای ارتباط با Telegram API + Bale API + Groq API + OpenCode Go API
- توکن ربات تلگرام از [@BotFather](https://t.me/BotFather)
- توکن ربات بله (از BotFather بله)
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
# توکن ربات تلگرام از BotFather (اجباری برای نسخهٔ تلگرام)
export BOT_TOKEN="1234567890:ABCDefghIJKLMNOpqrsTUVwxyz"

# توکن ربات بله (اجباری برای نسخهٔ بله)
export BALE_BOT_TOKEN="..."

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
python main.py         # نسخهٔ تلگرام
python main_bale.py    # نسخهٔ بله
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

> هر دو نسخه به یک دیتابیس (`data/knowledge.db`) وصل‌اند؛ می‌توانید هر دو را همزمان اجرا کنید.

---

## ۵. خطاهای رایج

| خطا | معنی | راه‌حل |
|---|---|---|
| `BOT_TOKEN not set` | توکن تنظیم نشده | `export BOT_TOKEN='...'` |
| `BALE_BOT_TOKEN` یافت نشد | توکن بله تنظیم نشده | متغیر را در unit فایل سرویس بله تنظیم کنید |
| `ModuleNotFoundError` | وابستگی نصب نشده | `pip install -r requirements.txt` |
| `no such column: title` | دیتابیس قدیمی | حذف `data/knowledge.db` و اجرای مجدد — یا migration خودکار |
| `HTTP 403 error code: 1010` | IP بلاک شده | کلاینت را با httpx امتحان کنید (urllib فرستنده نیست) |
| `'NoneType' object has no attribute 'reply_text'` | خطا در callback query | `update.effective_message` را جایگزین `update.message` کنید |

---

## ۶. استقرار روی VPS با systemd

مسیر پروژه روی سرور: `/root/MAPNAMD1-knowledge` — دو سرویس جداگانه:

```bash
# سرویس تلگرام (main.py)
sudo systemctl enable --now knowledgebot

# سرویس بله (main_bale.py)
sudo systemctl enable --now knowledgebot-bale

# بررسی وضعیت هر دو
sudo systemctl status knowledgebot knowledgebot-bale

# لاگ زنده
sudo journalctl -u knowledgebot -f          # تلگرام
sudo journalctl -u knowledgebot-bale -f     # بله
```

### جریان استقرار (deploy)

```bash
# لوکال:
git add -A && git commit -m "..." && git push origin main

# سرور:
ssh vps "cd /root/MAPNAMD1-knowledge && git pull && systemctl restart knowledgebot-bale knowledgebot"
```

> **نکته:** تغییرات ساختار دیتابیس هنگام ری‌استارت به‌صورت خودکار migration می‌شوند
> (ستون‌های جدید با `ALTER TABLE` اضافه می‌شوند؛ نیازی به حذف دیتابیس نیست).

---

## ۸. بکاپ و بازیابی

- **بکاپ خودکار:** هر روز ساعت ۳:۳۰ بامداد با cron روی سرور اجرا می‌شود
  (`scripts/backup_db.py`) → `/root/backups/knowledgebot/knowledge-YYYY-MM-DD.db.gz`
- از SQLite Online Backup API استفاده می‌کند (امن در حین فعالیت ربات‌ها) + بررسی integrity
- نگهداری: آخرین **۷ نسخهٔ روزانه**؛ قدیمی‌ترها خودکار حذف می‌شوند

### بازیابی دستی

```bash
# ۱. توقف سرویس‌ها
sudo systemctl stop knowledgebot-bale knowledgebot

# ۲. بازگردانی (مسیر بکاپ موردنظر)
gunzip -c /root/backups/knowledgebot/knowledge-YYYY-MM-DD.db.gz \
    > /root/MAPNAMD1-knowledge/data/knowledge.db

# ۳. اجرای مجدد
sudo systemctl start knowledgebot-bale knowledgebot
```

### اجرای دستی بکاپ

```bash
cd /root/MAPNAMD1-knowledge && .venv/bin/python scripts/backup_db.py
```

---

## ۹. وب‌اپ (مینی‌اپ)

- آدرس: `https://web.mohsekarim8.ir` (Cloudflare پروکسی + Origin Certificate تا ۲۰۴۱)
- زنجیره: CF edge:443 → Origin Rule (پورت ۲۰۸۳) → nginx → استاتیک `/var/www/knowledge-miniapp/` + پراکسی `/api/` → uvicorn 127.0.0.1:8010
- سرویس: `knowledgebot-webapp` — env از `/etc/knowledgebot/bale.env` و `webapp.env` (شامل `WEBAPP_SECRET` و `BOT_TOKEN`)
- ورود: فقط با initData معتبر از داخل بله/تلگرام؛ کاربر باید قبلاً در ربات ثبت‌نام کرده باشد

### به‌روزرسانی فرانت‌اند

```bash
cd /root/MAPNAMD1-knowledge && git pull && rsync -a --delete webapp/static/ /var/www/knowledge-miniapp/
```

---

## ۷. ساختار فایل‌ها

```
MAPNAMD1-knowledge/
├── main.py              ← نقطه ورود تلگرام (این را اجرا کن)
├── main_bale.py         ← نقطه ورود بله
├── config.py            ← تنظیمات (از env vars می‌خواند)
├── requirements.txt     ← وابستگی‌ها
├── db/                  ← لایه پایگاه داده (مشترک دو پلتفرم)
├── engine/              ← موتور هوش مصنوعی و منطق دانش
├── handlers/            ← هندلرهای تلگرام
├── bale_app/            ← هندلرها و فریم‌ورک بله
├── utils/               ← ابزارهای کمکی
├── data/                ← [runtime] فایل SQLite (مشترک دو ربات)
├── media/               ← [runtime] عکس‌ها و فایل‌ها
└── logs/                ← [runtime] فایل‌های لاگ
```