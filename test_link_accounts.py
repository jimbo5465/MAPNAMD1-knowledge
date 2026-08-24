# -*- coding: utf-8 -*-
"""تست لینک حساب‌های بله/تلگرام — با دیتابیس موقت (بدون دست زدن به data/knowledge.db)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TMP_DIR = tempfile.mkdtemp(prefix="kbtest_")
import config
config.DB_PATH = os.path.join(TMP_DIR, "knowledge.db")

from db import init as db_init
db_init.DB_PATH = config.DB_PATH  # get_connection از این ماژول استفاده می‌کند
db_init.init_db()

import db.models as m
from db.phone_utils import normalize_phone

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


print("== 1) نرمال‌سازی شماره ==")
cases = {
    "+989155107315": "9155107315",
    "989155107315": "9155107315",
    "09155107315": "9155107315",
    "9155107315": "9155107315",
    "+98 915 510 7315": "9155107315",
    "۰۰۹۸۹۱۵۵۱۰۷۳۱۵": "9155107315",
    "0915-510-7315": "9155107315",
}
for raw, expected in cases.items():
    check(f"{raw!r} → {expected}", normalize_phone(raw) == expected)
check("رشتهٔ نامعتبر → None", normalize_phone("hello") is None)
check("خالی → None", normalize_phone("") is None and normalize_phone(None) is None)

print("\n== 2) ثبت‌نام اول در تلگرام ==")
uid1, linked = m.register_or_link_user(
    platform="telegram", platform_id=111111,
    full_name="علی رضایی", phone="09155107315", personnel_code="1234",
)
check("ثبت جدید، بدون لینک", uid1 > 0 and not linked)

# مشاهده از تلگرام
m.add_observation(111111, "مشاهدهٔ تستی از تلگرام", tags=["تست"])
check("مشاهده ثبت شد", len(m.list_observations_by_user(111111)) == 1)

# پیش‌نویس دانش از تلگرام
kn = m.add_knowledge_entry(111111, "lesson", "علی رضایی")
check("پیش‌نویس دانش ثبت شد", kn > 0)
check("find_pending از تلگرام", m.find_pending_knowledge_by_user(111111)["id"] == kn)

print("\n== 3) ورود همان شخص از بله با قالب متفاوت شماره + کد پرسنلی ==")
uid2, linked = m.register_or_link_user(
    platform="bale", platform_id=222222,
    full_name="علی رضایی", phone="+989155107315", personnel_code="1234",
)
check("لینک انجام شد (رکورد جدید ساخته نشد)", linked and uid2 == uid1)
u = m.get_user_by_platform_id(222222)
check("کاربر با bale_id پیدا شد", u is not None and u["id"] == uid1)
check("bale_id ذخیره شد", u["bale_id"] == 222222)
check("telegram_id حفظ شد", u["telegram_id"] == 111111)
check("phone_norm یکسان", u["phone_norm"] == "9155107315")

print("\n== 4) دسترسی متقابل داده‌ها ==")
check("مشاهدات تلگرام از بله دیده شود", len(m.list_observations_by_user(222222)) == 1)
check("پیش‌نویس تلگرام از بله دیده شود", m.find_pending_knowledge_by_user(222222)["id"] == kn)
m.add_observation(222222, "مشاهدهٔ تستی از بله")
obs = m.list_observations_by_user(111111)
check("مشاهدهٔ بله از تلگرام دیده شود", len(obs) == 2)

print("\n== 5) امنیت: کد پرسنلی اشتباه → لینک نمی‌شود ==")
uid3, linked3 = m.register_or_link_user(
    platform="bale", platform_id=333333,
    full_name="هکر مشکوک", phone="989155107315", personnel_code="9999",
)
check("رکورد جدید جدا ساخته شد", not linked3 and uid3 != uid1)
check("مشاهدات اصلی لو نرفت", len(m.list_observations_by_user(333333)) == 0)

print("\n== 6) امنیت: شماره یکی ولی کد خالی → لینک نمی‌شود ==")
_, linked4 = m.register_or_link_user(
    platform="bale", platform_id=444444,
    full_name="بی‌کد", phone="09155107315", personnel_code=None,
)
check("بدون کد پرسنلی لینک نشد", not linked4)

print("\n== 7) سناریوی قدیمی: قبلاً در هر دو پلتفرم جدا ثبت شده ==")
# کاربر قدیمی: اول در بله ثبت شده (bale id داخل ستون telegram_id — سبک قدیمی)، بعد در تلگرام
legacy_bale_row = m.add_user(777777, "مریم احمدی", phone="09121234567", personnel_code="5678", bale_id=777777)
m.add_observation(777777, "مشاهدهٔ قدیمی بله")  # با owner key فعلی 777777
m.add_user(888888, "مریم احمدی", phone="+989121234567", personnel_code="5678")  # ثبت جدا در تلگرام
m.add_observation(888888, "مشاهدهٔ قدیمی تلگرام")

# حالا از تلگرام دوباره وارد می‌شود و فرم را پر می‌کند
uid_m, linked_m = m.register_or_link_user(
    platform="telegram", platform_id=888888,
    full_name="مریم احمدی", phone="۰۹۱۲۱۲۳۴۵۶۷", personnel_code="5678",
)
check("لینک به رکورد دارای حساب بله (اولویت)", linked_m and uid_m == legacy_bale_row)
merged = m.list_observations_by_user(888888)
check("هر دو مشاهدهٔ قدیمی ادغام شد", len(merged) == 2)
u_m = m.get_user_by_platform_id(888888)
check("رکورد واحد فعال", u_m is not None and u_m["is_active"] == 1)

print("\n== 8) ویرایش پروفایل با شناسهٔ بله ==")
m.update_user(222222, position="کارشناس فنی")
u2 = m.get_user_by_platform_id(222222)
check("position از طریق bale_id به‌روز شد", u2["position"] == "کارشناس فنی")

print("\n== 9) auth هر دو پلتفرم ==")
check("is_registered منطق: تلگرام", m.get_user_by_telegram_id(111111) is not None)
check("is_registered منطق: بله", m.get_user_by_telegram_id(222222) is not None)

print(f"\n{'='*50}\nنتیجه: {PASS} موفق، {FAIL} ناموفق")
sys.exit(1 if FAIL else 0)
