# AGENTS.md — راهنمای محیط برای دستیار کدنویسی

> این فایل را هر بار ابتدا بخوانید تا اطلاعات محیط دوباره کشف نشود.

---

## 🖥️ سیستم لوکال (ویندوز کاربر)

- **پاورشل بسته است** (Group Policy) — مستقیم از `cmd` استفاده کن، نه powershell.
  - به‌جای `Get-Content` → `type`، به‌جای `Test-NetConnection` → پایتون یا ssh.
  - خروجی فارسی در cmd: ابتدا `set PYTHONIOENCODING=utf-8`
- مسیر پروژه: `C:\Users\shaterian_m\Documents\Default Project\knowledge creation Bale BOT`
- Python سیستم: 3.14 (`python` در PATH)
- **ربات اصلی روی VPS اجرا می‌شود؛ سیستم لوکال فقط برای توسعه است.**
  - اجرای لوکال تستی (اختیاری): `run_bale.bat` (شامل توکن — gitignore شده)

## 🌐 VPS — سرور اصلی

| مورد | مقدار |
|---|---|
| اتصال SSH | `ssh vps` (در `%USERPROFILE%\.ssh\config` تعریف شده) |
| آیپی | `91.107.153.202` |
| **پورت SSH: ۴۴۳** | ⚠️ پورت‌های ۲۲/۵۳۲۲ و بقیه بسته‌اند — فقط 443 باز است |
| یوزر | `root` |
| هاست‌نیم | `ubuntu-2gb-nbg1-1` (Hetzner) |
| مسیر ریپو روی سرور | `/root/MAPNAMD1-knowledge` (نه /opt!) |
| venv سرور | `/root/MAPNAMD1-knowledge/.venv` |

### سرویس‌های systemd روی سرور
| سرویس | توضیح |
|---|---|
| `knowledgebot-bale.service` | **نسخه بله** (اصلی) — `main_bale.py` |
| `knowledgebot.service` | نسخه تلگرام — `main.py` |

### جریان استقرار (deploy)
```bash
# لوکال:
git add -A && git commit -m "..." && git push origin main

# سرور:
ssh vps "cd /root/MAPNAMD1-knowledge && git pull && systemctl restart knowledgebot-bale"

# لاگ زنده سرور:
ssh vps "journalctl -u knowledgebot-bale -f --no-pager"
```

- ریپو گیت‌هاب: `https://github.com/jimbo5465/MAPNAMD1-knowledge`
- پیام «BOT_TOKEN تنظیم نشده» هنگام import مربوط به نسخه تلگرام است — برای نسخه بله بی‌ضرر (از `BALE_BOT_TOKEN` داخل سرویس استفاده می‌شود).

## 📦 نکات کتابخانه bale (نسخه نصب‌شده ۲.۵)

- پراپرتی عکس `photos` است (جمع) — `photo` وجود ندارد.
- `voice` ندارد — ویس به شکل audio/document می‌آید.
- `answerCallbackQuery` در API بله وجود ندارد — لایه `bale_app/framework.py` شبیه‌سازی کرده.
- مارک‌داون پشتیبانی نمی‌شود — `strip_markdown` در framework متن را تمیز می‌کند.

## 💾 بکاپ (فعال از 1404/06)

- cron روزانه 3:30 سرور: `scripts/backup_db.py` → `/root/backups/knowledgebot/knowledge-YYYY-MM-DD.db.gz`
- نگهداری ۷ نسخه؛ SQLite online backup + integrity_check
- بازیابی: `gunzip -c <file> > data/knowledge.db` (سرویس‌ها متوقف)
- **TODO آینده:** ارسال بکاپ خارج از سرور (چت ادمین بله/تلگرام یا storage)، بکاپ پوشهٔ `media/`

## 🐛 تجربه‌های دیباگ قبلی

- «این دکمه دیگر معتبر نیست» = callback ناشناخته → حالا در `main_bale.py` لاگ می‌شود (unknown_callback_handler).
- Dispatcher سفارشی در `bale_app/framework.py` — state گفتگوها در حافظه است؛ ری‌استارت سرویس همه stateها را پاک می‌کند.

## 🔗 هویت دوپلتفرمی (لینک بله/تلگرام)

- یک کاربر واحد در هر دو پلتفرم: تطبیق `phone_norm` (۱۰ رقم آخر شماره) + کد پرسنلی — از `register_or_link_user` در `db/models.py`.
- توابع models که پارامتر `telegram_id` دارند، شناسهٔ هر دو پلتفرم را می‌پذیرند و داخلی resolve می‌کنند — هرگز مستقیم روی `observations.telegram_id` با شناسهٔ خام کوئری نزن.
- تست‌ها: `test_link_accounts.py` و `test_migration.py` (با دیتابیس موقت، بی‌خطر برای data/knowledge.db).
