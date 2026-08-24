# -*- coding: utf-8 -*-
"""تست بایگانی دانش — list_knowledge_by_user / search_knowledge_by_user (دیتابیس موقت)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TMP_DIR = tempfile.mkdtemp(prefix="kbarch_")
import config
config.DB_PATH = os.path.join(TMP_DIR, "knowledge.db")

from db import init as db_init
db_init.DB_PATH = config.DB_PATH
db_init.init_db()

import db.models as m

PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}")


print("== آماده‌سازی ==")
uid, _ = m.register_or_link_user(
    platform="telegram", platform_id=1001,
    full_name="سارا", phone="09151112233", personnel_code="A10",
)
kn1 = m.add_knowledge_entry(1001, "lesson", "سارا")
m.set_knowledge_fields(kn1, {"title": "رفع نشتی شیر اطمینان"}, draft_text="شرح کامل نشتی و راه‌حل")
m.submit_knowledge_entry(kn1, "KN-MAP-1404-001", "x.pdf", "x.docx")

kn2 = m.add_knowledge_entry(1001, "suggestion", "سارا")
m.set_knowledge_fields(kn2, {"title": "پیشنهاد تغییر روغن"}, draft_text=None)

uid2, _ = m.register_or_link_user(
    platform="bale", platform_id=2002,
    full_name="رضا", phone="+989121234567", personnel_code="B20",
)
kn3 = m.add_knowledge_entry(2002, "explicit", "رضا")
m.set_knowledge_fields(kn3, {"title": "معرفی استاندارد جوش"}, draft_text="استاندارد AWS")

print("\n== list_knowledge_by_user ==")
lst = m.list_knowledge_by_user(1001)
check("دو دانش برای سارا", len(lst) == 2)
check("ترتیب جدیدترین اول", lst[0]["id"] == kn2)

print("\n== دسترسی متقابل (لینک حساب) ==")
# رضا از بله؛ حالا از تلگرام هم وارد شود → همان دانش‌ها
check("لیست از طریق پلتفرم دیگر", len(m.list_knowledge_by_user(2002)) == 1)

print("\n== search_knowledge_by_user ==")
r = m.search_knowledge_by_user(1001, "نشتی")
check("جستجوی عنوان در fields_json", len(r) == 1 and r[0]["id"] == kn1)
r = m.search_knowledge_by_user(1001, "KN-MAP")
check("جستجوی شمارهٔ دانش", len(r) == 1)
r = m.search_knowledge_by_user(1001, "روغن")
check("جستجوی پیش‌نویس دوم", len(r) == 1 and r[0]["id"] == kn2)
check("جستجوی بی‌نتیجه", m.search_knowledge_by_user(1001, "توربین") == [])
check("جستجو با کلیدواژهٔ خالی", m.search_knowledge_by_user(1001, "   ") == [])
check("کاربر ناشناس", m.search_knowledge_by_user(99999, "نشتی") == [])

print("\n== جداسازی داده کاربران ==")
check("دانش رضا در لیست سارا نیست", all(e["id"] != kn3 for e in m.list_knowledge_by_user(1001)))
check("جستجوی سارا دانش رضا را نمی‌بیند", all(e["id"] != kn3 for e in m.search_knowledge_by_user(1001, "جوش")))

print(f"\n{'='*50}\nنتیجه: {PASS} موفق، {FAIL} ناموفق")
sys.exit(1 if FAIL else 0)
