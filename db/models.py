"""
مدل‌های پایگاه داده MAPNAMD1-knowledge.
شامل:
  - کاربران (پروفایل ساده: نام، شماره، کد پرسنلی، پروژه، سمت)
  - دانش‌های ثبت‌شده (knowledge_entries)
  - عکس‌های دانش
  - مشاهدات صحرایی (observations)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from db.init import get_connection


# ─── کمکی ────────────────────────────────────────────────────────────────────

def _now_str() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════════
# کاربران (پروفایل)
# ══════════════════════════════════════════════════════════════════════════════

def add_user(
    telegram_id: int,
    full_name: str,
    phone: str | None = None,
    personnel_code: str | None = None,
    project_name: str | None = None,
    position: str | None = None,
) -> int:
    """کاربر جدید ثبت می‌کند. اگر telegram_id تکراری باشد، به‌روز می‌کند."""
    with get_connection() as conn:
        now = _now_str()
        cur = conn.execute(
            """
            INSERT INTO users (telegram_id, full_name, phone, personnel_code,
                               project_name, position, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                full_name = excluded.full_name,
                phone = excluded.phone,
                personnel_code = excluded.personnel_code,
                project_name = excluded.project_name,
                position = excluded.position,
                updated_at = excluded.updated_at
            """,
            (telegram_id, full_name, phone, personnel_code,
             project_name, position, now, now),
        )
        conn.commit()
        return cur.lastrowid


def get_user_by_telegram_id(telegram_id: int) -> dict | None:
    """کاربر را با telegram_id پیدا می‌کند."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ? AND is_active = 1",
            (telegram_id,),
        ).fetchone()
        return _row_to_dict(row)


def update_user(telegram_id: int, **fields) -> None:
    """به‌روزرسانی فیلدهای کاربر (full_name, phone, personnel_code, project_name, position)."""
    allowed = {"full_name", "phone", "personnel_code", "project_name", "position"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return
    updates["updated_at"] = _now_str()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [telegram_id]
    with get_connection() as conn:
        conn.execute(
            f"UPDATE users SET {set_clause} WHERE telegram_id = ?",
            values,
        )
        conn.commit()


# ══════════════════════════════════════════════════════════════════════════════
# دانش‌های ثبت‌شده (knowledge_entries)
# ══════════════════════════════════════════════════════════════════════════════

def _deserialize_knowledge(row: sqlite3.Row | None) -> dict | None:
    """ردیف knowledge_entries را به dict تبدیل می‌کند؛ JSONها را از هم باز می‌کند."""
    if row is None:
        return None
    d = dict(row)
    if isinstance(d.get("fields_json"), str):
        try:
            d["fields_json"] = json.loads(d["fields_json"])
        except (json.JSONDecodeError, TypeError):
            d["fields_json"] = {}
    else:
        d["fields_json"] = {}
    if isinstance(d.get("interview_history_json"), str):
        try:
            d["interview_history_json"] = json.loads(d["interview_history_json"])
        except (json.JSONDecodeError, TypeError):
            d["interview_history_json"] = []
    else:
        d["interview_history_json"] = []
    for jkey in ("tree_path_json", "org_metadata_json", "extra_data"):
        if isinstance(d.get(jkey), str):
            try:
                d[jkey] = json.loads(d[jkey])
            except (json.JSONDecodeError, TypeError):
                d[jkey] = {} if jkey != "tree_path_json" else []
        elif d.get(jkey) is None:
            d[jkey] = {} if jkey != "tree_path_json" else []
    return d


def add_knowledge_entry(
    telegram_id: int,
    knowledge_type: str,
    reporter_name: str,
    reporter_title: str | None = None,
    fields: dict | None = None,
    draft_text: str | None = None,
    raw_description: str | None = None,
    reported_date: str | None = None,
    extra_data: dict | None = None,
) -> int:
    """یک رکورد دانش جدید ایجاد می‌کند (وضعیت: draft)."""
    now = _now_str()
    with get_connection() as conn:
        # پیدا کردن user_id از telegram_id
        user = get_user_by_telegram_id(telegram_id)
        if user is None:
            raise ValueError(f"کاربر با telegram_id={telegram_id} یافت نشد.")
        user_id = user["id"]
        cur = conn.execute(
            """
            INSERT INTO knowledge_entries (
                project_id, contractor_id, status, knowledge_type,
                reporter_name, reporter_title, reported_by,
                raw_description, fields_json, draft_text,
                reported_date,
                is_active, created_at, extra_data
            ) VALUES (
                NULL, NULL, 'draft', ?,
                ?, ?, ?,
                ?, ?, ?,
                ?,
                1, ?, ?
            )
            """,
            (
                knowledge_type,
                reporter_name, reporter_title, user_id,
                raw_description,
                json.dumps(fields or {}, ensure_ascii=False),
                draft_text,
                reported_date,
                now,
                json.dumps(extra_data or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
        return cur.lastrowid


def get_knowledge_entry_by_id(knowledge_id: int) -> dict | None:
    """یک رکورد دانش را با شناسه برمی‌گرداند."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM knowledge_entries WHERE id = ?",
            (knowledge_id,),
        ).fetchone()
        return _deserialize_knowledge(row)


def set_knowledge_fields(knowledge_id: int, fields: dict, draft_text: str | None = None) -> None:
    """فیلدهای ساختاریافته و اختیاراً پیش‌نویس را به‌روز می‌کند."""
    with get_connection() as conn:
        if draft_text:
            conn.execute(
                "UPDATE knowledge_entries SET fields_json = ?, draft_text = ? WHERE id = ?",
                (json.dumps(fields, ensure_ascii=False), draft_text, knowledge_id),
            )
        else:
            conn.execute(
                "UPDATE knowledge_entries SET fields_json = ? WHERE id = ?",
                (json.dumps(fields, ensure_ascii=False), knowledge_id),
            )
        conn.commit()


def submit_knowledge_entry(knowledge_id: int, kn_number: str, pdf_path: str, docx_path: str) -> None:
    """دانش را نهایی می‌کند (draft → submitted)."""
    now = _now_str()
    with get_connection() as conn:
        conn.execute(
            """UPDATE knowledge_entries
               SET status = 'submitted', kn_number = ?, submitted_at = ?,
                   pdf_path = ?, docx_path = ?
               WHERE id = ?""",
            (kn_number, now, pdf_path, docx_path, knowledge_id),
        )
        conn.commit()


def set_knowledge_inactive(knowledge_id: int) -> None:
    """دانش را غیرفعال می‌کند (لغو پیش‌نویس)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE knowledge_entries SET is_active = 0 WHERE id = ?",
            (knowledge_id,),
        )
        conn.commit()


def list_knowledge_entries(active_only: bool = True) -> list[dict]:
    """فهرست تمام دانش‌ها (جدیدترین اول)."""
    with get_connection() as conn:
        where = "WHERE is_active = 1" if active_only else ""
        rows = conn.execute(
            f"SELECT * FROM knowledge_entries {where} ORDER BY id DESC"
        ).fetchall()
        return [_deserialize_knowledge(r) for r in rows]


def add_knowledge_photo(knowledge_id: int, path: str) -> int:
    """عکسی به یک دانش اضافه می‌کند."""
    now = _now_str()
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO knowledge_photos (knowledge_id, path, uploaded_at) VALUES (?, ?, ?)",
            (knowledge_id, path, now),
        )
        conn.commit()
        return cur.lastrowid


def list_knowledge_photos(knowledge_id: int) -> list[dict]:
    """فهرست عکس‌های یک دانش."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM knowledge_photos WHERE knowledge_id = ? ORDER BY id",
            (knowledge_id,),
        ).fetchall()
        return _rows_to_dicts(rows)


def set_knowledge_interview_history(knowledge_id: int, history: list) -> None:
    """تاریخچهٔ مصاحبه را ذخیره می‌کند."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE knowledge_entries SET interview_history_json = ? WHERE id = ?",
            (json.dumps(history, ensure_ascii=False), knowledge_id),
        )
        conn.commit()


def get_knowledge_interview_history(knowledge_id: int) -> list:
    """تاریخچهٔ مصاحبه را برمی‌گرداند."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT interview_history_json FROM knowledge_entries WHERE id = ?",
            (knowledge_id,),
        ).fetchone()
        if row is None:
            return []
        raw = row["interview_history_json"]
        if not raw:
            return []
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []


def set_knowledge_tree_path(knowledge_id: int, path: list[str]) -> None:
    """مسیر درخت دانش را ذخیره می‌کند."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE knowledge_entries SET tree_path_json = ? WHERE id = ?",
            (json.dumps(path, ensure_ascii=False), knowledge_id),
        )
        conn.commit()


def get_knowledge_tree_path(knowledge_id: int) -> list[str]:
    """مسیر درخت دانش را برمی‌گرداند."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT tree_path_json FROM knowledge_entries WHERE id = ?",
            (knowledge_id,),
        ).fetchone()
        if row is None:
            return []
        raw = row["tree_path_json"]
        if not raw:
            return []
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []


def set_knowledge_org_metadata(knowledge_id: int, org_data: dict) -> None:
    """فرادادهٔ سازمانی را ذخیره می‌کند."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE knowledge_entries SET org_metadata_json = ? WHERE id = ?",
            (json.dumps(org_data, ensure_ascii=False), knowledge_id),
        )
        conn.commit()


def get_knowledge_org_metadata(knowledge_id: int) -> dict:
    """فرادادهٔ سازمانی را برمی‌گرداند."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT org_metadata_json FROM knowledge_entries WHERE id = ?",
            (knowledge_id,),
        ).fetchone()
        if row is None:
            return {}
        raw = row["org_metadata_json"]
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}


def find_pending_knowledge_by_user(telegram_id: int) -> dict | None:
    """پیش‌نویس فعال (draft) کاربر را پیدا می‌کند."""
    with get_connection() as conn:
        row = conn.execute(
            """SELECT ke.* FROM knowledge_entries ke
               JOIN users u ON u.id = ke.reported_by
               WHERE u.telegram_id = ? AND ke.status = 'draft' AND ke.is_active = 1
               ORDER BY ke.id DESC LIMIT 1""",
            (telegram_id,),
        ).fetchone()
        return _deserialize_knowledge(row)


# ══════════════════════════════════════════════════════════════════════════════
# مشاهدات صحرایی (observations)
# ══════════════════════════════════════════════════════════════════════════════

def add_observation(
    telegram_id: int,
    content: str,
    project_name: str | None = None,
    tags: list[str] | None = None,
) -> int:
    """یک مشاهدهٔ جدید ثبت می‌کند (وضعیت: raw)."""
    now = _now_str()
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO observations (telegram_id, content, status,
               project_name, tags, created_at, updated_at)
               VALUES (?, ?, 'raw', ?, ?, ?, ?)""",
            (telegram_id, content, project_name,
             json.dumps(tags or [], ensure_ascii=False), now, now),
        )
        conn.commit()
        return cur.lastrowid


def list_observations_by_user(telegram_id: int, status: str | None = None) -> list[dict]:
    """مشاهدات یک کاربر (جدیدترین اول)."""
    with get_connection() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM observations WHERE telegram_id = ? AND status = ? ORDER BY id DESC",
                (telegram_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM observations WHERE telegram_id = ? ORDER BY id DESC",
                (telegram_id,),
            ).fetchall()
        return _rows_to_dicts(rows)


def get_observation_by_id(obs_id: int) -> dict | None:
    """یک مشاهده را با شناسه برمی‌گرداند."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM observations WHERE id = ?",
            (obs_id,),
        ).fetchone()
        return _row_to_dict(row)


def update_observation(obs_id: int, content: str | None = None,
                       tags: list[str] | None = None) -> None:
    """به‌روزرسانی محتوای مشاهده."""
    now = _now_str()
    with get_connection() as conn:
        if content:
            conn.execute(
                "UPDATE observations SET content = ?, updated_at = ? WHERE id = ?",
                (content, now, obs_id),
            )
        if tags is not None:
            conn.execute(
                "UPDATE observations SET tags = ?, updated_at = ? WHERE id = ?",
                (json.dumps(tags, ensure_ascii=False), now, obs_id),
            )
        conn.commit()


def promote_observation(obs_id: int, knowledge_id: int) -> None:
    """یک مشاهده را به دانش ارتقا می‌دهد (وضعیت: promoted)."""
    now = _now_str()
    with get_connection() as conn:
        conn.execute(
            "UPDATE observations SET status = 'promoted', promoted_to_kn_id = ?, updated_at = ? WHERE id = ?",
            (knowledge_id, now, obs_id),
        )
        conn.commit()


def archive_observation(obs_id: int) -> None:
    """مشاهده را بایگانی می‌کند."""
    now = _now_str()
    with get_connection() as conn:
        conn.execute(
            "UPDATE observations SET status = 'archived', updated_at = ? WHERE id = ?",
            (now, obs_id),
        )
        conn.commit()


# ── پیوست‌های مشاهده (عکس/PDF/فایل) ──────────────────────────────────────────

def add_observation_attachment(
    observation_id: int,
    file_path: str,
    file_name: str | None = None,
    mime_type: str | None = None,
    file_size: int | None = None,
) -> int:
    """یک پیوست به مشاهده اضافه می‌کند."""
    now = _now_str()
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO observation_attachments
               (observation_id, file_path, file_name, mime_type, file_size, uploaded_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (observation_id, file_path, file_name, mime_type, file_size, now),
        )
        conn.commit()
        return cur.lastrowid


def list_observation_attachments(observation_id: int) -> list[dict]:
    """پیوست‌های یک مشاهده."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM observation_attachments WHERE observation_id = ? ORDER BY id",
            (observation_id,),
        ).fetchall()
        return _rows_to_dicts(rows)


def remove_observation_attachment(attachment_id: int) -> None:
    """یک پیوست را حذف می‌کند."""
    with get_connection() as conn:
        conn.execute("DELETE FROM observation_attachments WHERE id = ?", (attachment_id,))
        conn.commit()