"""
ماژول init — ساخت جداول پایگاه داده و مقداردهی اولیه برای MAPNAMD1-knowledge.
فقط جداول مربوط به دانش سازمانی:
  - users             (پروفایل کاربران: نام، شماره تماس، کد پرسنلی، پروژه، سمت)
  - knowledge_entries (دانش ثبت‌شده)
  - knowledge_photos  (عکس‌های دانش)
  - observations      (مشاهدات صحرایی خام)
این ماژول فقط از config import می‌کند.
"""

from __future__ import annotations

import sqlite3
from config import DB_PATH


def get_connection() -> sqlite3.Connection:
    """
    اتصال به پایگاه داده SQLite را با تنظیمات استاندارد برمی‌گرداند.

    ویژگی‌ها:
        - row_factory = sqlite3.Row (دسترسی به ستون‌ها با نام)
        - PRAGMA foreign_keys = ON (اعمال محدودیت‌های کلید خارجی)
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate_knowledge_phase3(cur: sqlite3.Cursor) -> None:
    """idempotent migration برای فاز۳ دانش."""
    cols = [row[1] for row in cur.execute("PRAGMA table_info(knowledge_entries)").fetchall()]
    if not cols:
        return

    needs_rebuild = False

    if "interview_history_json" not in cols:
        cur.execute("ALTER TABLE knowledge_entries ADD COLUMN interview_history_json TEXT")
    if "tree_path_json" not in cols:
        cur.execute("ALTER TABLE knowledge_entries ADD COLUMN tree_path_json TEXT")
    if "org_metadata_json" not in cols:
        cur.execute("ALTER TABLE knowledge_entries ADD COLUMN org_metadata_json TEXT")

    # Check if project_id or contractor_id are NOT NULL
    table_info = cur.execute("PRAGMA table_info(knowledge_entries)").fetchall()
    for row in table_info:
        if row[1] in ("project_id", "contractor_id"):
            if row[3] == 1:  # notnull is 1
                needs_rebuild = True

    if needs_rebuild:
        cur.execute("PRAGMA foreign_keys = OFF")

        cur.execute(
            """
            CREATE TABLE knowledge_entries_new (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                kn_number                TEXT UNIQUE,
                project_id               INTEGER,
                contractor_id            INTEGER,
                status                   TEXT NOT NULL DEFAULT 'draft'
                                         CHECK (status IN ('draft', 'submitted')),
                knowledge_type           TEXT NOT NULL
                                         CHECK (knowledge_type IN ('lesson','suggestion','explicit')),
                reporter_name            TEXT NOT NULL,
                reporter_title           TEXT,
                reported_by              INTEGER NOT NULL REFERENCES users(id),
                raw_description          TEXT,
                fields_json              TEXT,
                draft_text               TEXT,
                reported_date            TEXT,
                submitted_at             TEXT,
                pdf_path                 TEXT,
                docx_path                TEXT,
                is_active                INTEGER NOT NULL DEFAULT 1
                                         CHECK (is_active IN (0, 1)),
                created_at               TEXT NOT NULL,
                interview_history_json   TEXT,
                tree_path_json           TEXT,
                org_metadata_json        TEXT,
                extra_data               TEXT
            )
            """
        )

        cur.execute(
            """
            INSERT INTO knowledge_entries_new
            SELECT id, kn_number, project_id, contractor_id, status, knowledge_type,
                   reporter_name, reporter_title, reported_by, raw_description,
                   fields_json, draft_text, reported_date, submitted_at, pdf_path,
                   docx_path, is_active, created_at, interview_history_json, tree_path_json, org_metadata_json, extra_data
            FROM knowledge_entries
            """
        )

        cur.execute("DROP TABLE knowledge_entries")
        cur.execute("ALTER TABLE knowledge_entries_new RENAME TO knowledge_entries")

        cur.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_entries_status ON knowledge_entries(status)")

        cur.execute("PRAGMA foreign_keys = ON")


def init_db() -> None:
    """ساخت تمام جداول لازم (idempotent — اجرای مکرر بی‌خطر است)."""
    with get_connection() as conn:
        cur = conn.cursor()

        # ── کاربران ──────────────────────────────────────────────────────
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id       INTEGER NOT NULL UNIQUE,
                full_name         TEXT    NOT NULL,
                phone             TEXT,
                personnel_code    TEXT,
                project_name      TEXT,
                position          TEXT,
                is_active         INTEGER NOT NULL DEFAULT 1
                                  CHECK (is_active IN (0, 1)),
                created_at        TEXT NOT NULL,
                updated_at        TEXT
            )
            """
        )

        # ── دانش‌های ثبت‌شده ─────────────────────────────────────────────
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_entries (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                kn_number                TEXT UNIQUE,
                project_id               INTEGER,
                contractor_id            INTEGER,
                status                   TEXT NOT NULL DEFAULT 'draft'
                                         CHECK (status IN ('draft', 'submitted')),
                knowledge_type           TEXT NOT NULL
                                         CHECK (knowledge_type IN ('lesson','suggestion','explicit')),
                reporter_name            TEXT NOT NULL,
                reporter_title           TEXT,
                reported_by              INTEGER NOT NULL REFERENCES users(id),
                raw_description          TEXT,
                fields_json              TEXT,
                draft_text               TEXT,
                reported_date            TEXT,
                submitted_at             TEXT,
                pdf_path                 TEXT,
                docx_path                TEXT,
                is_active                INTEGER NOT NULL DEFAULT 1
                                         CHECK (is_active IN (0, 1)),
                created_at               TEXT NOT NULL,
                interview_history_json   TEXT,
                tree_path_json           TEXT,
                org_metadata_json        TEXT,
                extra_data               TEXT
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_entries_status ON knowledge_entries(status)")

        # ── Migration دانش فاز ۳ ──────────────────────────────────────────
        _migrate_knowledge_phase3(cur)

        # ── عکس‌های ثبت دانش ─────────────────────────────────────────────
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_photos (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                knowledge_id INTEGER NOT NULL REFERENCES knowledge_entries(id),
                path         TEXT NOT NULL,
                uploaded_at  TEXT NOT NULL
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_photos_knowledge ON knowledge_photos(knowledge_id)")

        # ── مشاهدات صحرایی ───────────────────────────────────────────────
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS observations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id     INTEGER NOT NULL,
                title           TEXT,
                content         TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'raw'
                                CHECK (status IN ('raw', 'maturing', 'promoted', 'archived')),
                promoted_to_kn_id INTEGER REFERENCES knowledge_entries(id),
                project_name    TEXT,
                tags            TEXT,
                obs_date        TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_observations_telegram ON observations(telegram_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_observations_status ON observations(status)")

        # ── migration برای دیتابیس‌های قدیمی (قبل از افزودن title/obs_date) ──
        # باید قبل از CREATE INDEX روی obs_date باشد
        cols = [row[1] for row in cur.execute("PRAGMA table_info(observations)").fetchall()]
        if cols and "title" not in cols:
            cur.execute("ALTER TABLE observations ADD COLUMN title TEXT")
        if cols and "obs_date" not in cols:
            cur.execute("ALTER TABLE observations ADD COLUMN obs_date TEXT")

        cur.execute("CREATE INDEX IF NOT EXISTS idx_observations_obs_date ON observations(obs_date)")

        # ── پیوست‌های مشاهدات (عکس/PDF/فایل) ─────────────────────────────
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS observation_attachments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                observation_id  INTEGER NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
                file_path       TEXT NOT NULL,
                file_name       TEXT,
                mime_type       TEXT,
                file_size       INTEGER,
                uploaded_at     TEXT NOT NULL
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_obs_attachments_obs ON observation_attachments(observation_id)"
        )

        conn.commit()
