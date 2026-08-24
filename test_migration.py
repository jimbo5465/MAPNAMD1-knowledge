# -*- coding: utf-8 -*-
"""تست migration: دیتابیس با اسکیمای قدیمی (بدون bale_id/phone_norm) → init_db جدید"""
import os, sqlite3, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TMP_DIR = tempfile.mkdtemp(prefix="kbmig_")
import config
config.DB_PATH = os.path.join(TMP_DIR, "knowledge.db")

# ساخت دیتابیس با اسکیمای قدیمی (بدون ستون‌های جدید)
conn = sqlite3.connect(config.DB_PATH)
conn.executescript("""
CREATE TABLE users (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id       INTEGER NOT NULL UNIQUE,
    full_name         TEXT    NOT NULL,
    phone             TEXT,
    personnel_code    TEXT,
    project_name      TEXT,
    position          TEXT,
    is_active         INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at        TEXT NOT NULL,
    updated_at        TEXT
);
CREATE TABLE knowledge_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kn_number TEXT UNIQUE,
    project_id INTEGER, contractor_id INTEGER,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','submitted')),
    knowledge_type TEXT NOT NULL CHECK (knowledge_type IN ('lesson','suggestion','explicit')),
    reporter_name TEXT NOT NULL, reporter_title TEXT,
    reported_by INTEGER NOT NULL REFERENCES users(id),
    raw_description TEXT, fields_json TEXT, draft_text TEXT,
    reported_date TEXT, submitted_at TEXT, pdf_path TEXT, docx_path TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at TEXT NOT NULL, interview_history_json TEXT,
    tree_path_json TEXT, org_metadata_json TEXT, extra_data TEXT
);
INSERT INTO users (telegram_id, full_name, phone, personnel_code, is_active, created_at)
VALUES (555000, 'کاربر قدیمی', '+98 935 123 4567', '7788', 1, '2025-01-01');
""")
conn.commit()
conn.close()

# حالا init_db با اسکیمای جدید باید migration کند
from db import init as db_init
db_init.DB_PATH = config.DB_PATH
db_init.init_db()

conn = sqlite3.connect(config.DB_PATH)
conn.row_factory = sqlite3.Row
cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
assert "bale_id" in cols, f"bale_id اضافه نشد: {cols}"
assert "phone_norm" in cols, f"phone_norm اضافه نشد: {cols}"
row = conn.execute("SELECT phone_norm FROM users WHERE telegram_id=555000").fetchone()
assert row["phone_norm"] == "9351234567", f"backfill غلط: {row['phone_norm']!r}"

# کاربر قدیمی باید با شناسه پلتفرم قابل پیدا کردن باشد و لینک بله هم رویش ممکن باشد
import db.models as m
u = m.get_user_by_platform_id(555000)
assert u is not None and u["full_name"] == "کاربر قدیمی"
match = m.find_linkable_user("09351234567", "7788")
assert match is not None and match["id"] == u["id"], "find_linkable_user روی رکورد مهاجرت‌شده کار نکرد"

print("[PASS] migration کامل: ستون‌ها اضافه، phone_norm پر شد، تطبیق روی رکورد قدیمی کار می‌کند")
