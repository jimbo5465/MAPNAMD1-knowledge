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
from db.phone_utils import normalize_phone


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
    bale_id: int | None = None,
) -> int:
    """کاربر جدید ثبت می‌کند. اگر telegram_id تکراری باشد، به‌روز می‌کند."""
    with get_connection() as conn:
        now = _now_str()
        cur = conn.execute(
            """
            INSERT INTO users (telegram_id, bale_id, full_name, phone, phone_norm,
                               personnel_code, project_name, position,
                               is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                bale_id = COALESCE(excluded.bale_id, users.bale_id),
                full_name = excluded.full_name,
                phone = excluded.phone,
                phone_norm = COALESCE(excluded.phone_norm, users.phone_norm),
                personnel_code = excluded.personnel_code,
                project_name = excluded.project_name,
                position = excluded.position,
                updated_at = excluded.updated_at
            """,
            (telegram_id, bale_id, full_name, phone, normalize_phone(phone),
             personnel_code, project_name, position, now, now),
        )
        conn.commit()
        return cur.lastrowid


def get_user_by_platform_id(platform_id: int) -> dict | None:
    """
    کاربر را با شناسهٔ هر پلتفرم پیدا می‌کند (telegram_id یا bale_id).
    این تابع هستهٔ لینک حساب‌هاست — یک رکورد کاربر ممکن است هر دو شناسه را داشته باشد.
    """
    with get_connection() as conn:
        row = conn.execute(
            """SELECT * FROM users
               WHERE is_active = 1 AND (telegram_id = ? OR bale_id = ?)
               ORDER BY CASE WHEN telegram_id = ? THEN 0 ELSE 1 END
               LIMIT 1""",
            (platform_id, platform_id, platform_id),
        ).fetchone()
        return _row_to_dict(row)


def get_user_by_telegram_id(telegram_id: int) -> dict | None:
    """کاربر را با شناسهٔ پلتفرم پیدا می‌کند (سازگاری قدیمی — جستجو در هر دو ستون)."""
    return get_user_by_platform_id(telegram_id)


def get_user_by_db_id(db_id: int) -> dict | None:
    """کاربر را با شناسهٔ داخلی دیتابیس (users.id) پیدا می‌کند."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ? AND is_active = 1",
            (db_id,),
        ).fetchone()
        return _row_to_dict(row)


def update_user(platform_id: int, **fields) -> None:
    """به‌روزرسانی فیلدهای کاربر (full_name, phone, personnel_code, project_name, position).
    ورودی می‌تواند شناسهٔ تلگرام یا بله باشد."""
    allowed = {"full_name", "phone", "personnel_code", "project_name", "position"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    user = get_user_by_platform_id(platform_id)
    if not updates or user is None:
        return
    norm = normalize_phone(updates["phone"]) if "phone" in updates else None
    if norm:
        updates["phone_norm"] = norm
    updates["updated_at"] = _now_str()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [user["id"]]
    with get_connection() as conn:
        conn.execute(
            f"UPDATE users SET {set_clause} WHERE id = ?",
            values,
        )
        conn.commit()


# ══════════════════════════════════════════════════════════════════════════════
# لینک حساب‌های بله و تلگرام — تطبیق با شمارهٔ نرمال‌شده + کد پرسنلی
# ══════════════════════════════════════════════════════════════════════════════

def find_linkable_user(phone: str | None, personnel_code: str | None) -> dict | None:
    """
    کاربر موجودی را پیدا می‌کند که شمارهٔ نرمال‌شده و کد پرسنلی‌اش
    با ورودی مطابقت دارد (هر دو شرط الزامی — کد پرسنلی به‌عنوان لایهٔ امنیتی).
    اولویت با رکوردهایی است که از قبل حساب بله دارند.
    """
    norm = normalize_phone(phone)
    pc = (personnel_code or "").strip()
    if not norm or not pc:
        return None
    with get_connection() as conn:
        row = conn.execute(
            """SELECT * FROM users
               WHERE is_active = 1 AND phone_norm = ?
                 AND TRIM(personnel_code) = ?
               ORDER BY CASE WHEN bale_id IS NOT NULL THEN 0 ELSE 1 END, id DESC
               LIMIT 1""",
            (norm, pc),
        ).fetchone()
        return _row_to_dict(row)


def link_platform_account(db_user_id: int, platform_id: int, platform: str) -> None:
    """شناسهٔ پلتفرم (bale/telegram) را به رکورد موجود کاربر متصل می‌کند."""
    if platform == "bale":
        col = "bale_id"
    elif platform == "telegram":
        col = "telegram_id"
    else:
        raise ValueError(f"پلتفرم نامعتبر: {platform!r}")
    with get_connection() as conn:
        conn.execute(
            f"UPDATE users SET {col} = ?, updated_at = ? WHERE id = ?",
            (platform_id, _now_str(), db_user_id),
        )
        conn.commit()


def _reassign_observations_owner(from_key: int, to_key: int) -> None:
    """کلید مالکیت مشاهدات را تغییر می‌دهد (موقع ادغام حساب‌ها)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE observations SET telegram_id = ? WHERE telegram_id = ?",
            (to_key, from_key),
        )
        conn.commit()


def deactivate_duplicate_accounts(
    platform_id: int,
    keep_db_id: int,
    future_owner_key: int | None = None,
) -> int:
    """
    رکوردهای تکراری قدیمی (ثبت جداگانهٔ یک شخص در دو پلتفرم قبل از قابلیت لینک)
    را غیرفعال می‌کند و دانش/مشاهداتشان را به حساب اصلی منتقل می‌کند.
    future_owner_key: اگر لینک قرار است telegram_id رکورد اصلی را عوض کند،
    کلید مالکیت نهایی را همینجا بدهید تا داده‌ها به مقدار جدید منتقل شوند.
    خروجی: تعداد رکوردهای ادغام‌شده.
    """
    moved = 0
    with get_connection() as conn:
        keep = conn.execute("SELECT * FROM users WHERE id = ?", (keep_db_id,)).fetchone()
        if keep is None:
            return 0
        owner_key = future_owner_key if future_owner_key is not None else keep["telegram_id"]
        dups = conn.execute(
            """SELECT * FROM users
               WHERE is_active = 1 AND id != ?
                 AND (telegram_id = ? OR bale_id = ?)""",
            (keep_db_id, platform_id, platform_id),
        ).fetchall()
        for dup in dups:
            conn.execute(
                "UPDATE knowledge_entries SET reported_by = ? WHERE reported_by = ?",
                (keep_db_id, dup["id"]),
            )
            conn.execute(
                "UPDATE observations SET telegram_id = ? WHERE telegram_id = ?",
                (owner_key, dup["telegram_id"]),
            )
            # رکورد غیرفعال نباید مقدار پلتفرمیِ در حال لینک را نگه دارد
            # (قید UNIQUE) — شناسهٔ تلگرام به sentinel منفی تغییر می‌کند.
            # شناسه‌های واقعی پلتفرم همیشه مثبت هستند، پس تداخلی پیش نمی‌آید.
            new_tg = -(dup["id"]) if dup["telegram_id"] == platform_id else dup["telegram_id"]
            conn.execute(
                "UPDATE users SET is_active = 0, telegram_id = ?, bale_id = NULL, updated_at = ? WHERE id = ?",
                (new_tg, _now_str(), dup["id"]),
            )
            moved += 1
        conn.commit()
    return moved


def register_or_link_user(
    *,
    platform: str,
    platform_id: int,
    full_name: str,
    phone: str | None = None,
    personnel_code: str | None = None,
    project_name: str | None = None,
    position: str | None = None,
) -> tuple[int, bool]:
    """
    ثبت‌نام جدید یا اتصال به حساب موجود.
    اگر کاربری با همان شمارهٔ نرمال‌شده + کد پرسنلی قبلاً ثبت شده باشد،
    حساب فعلی به آن رکورد لینک می‌شود (خروجی: linked=True) و
    رکوردهای تکراری قدیمی ادغام می‌شوند.
    خروجی: (user_db_id، آیا لینک شد؟)
    """
    match = find_linkable_user(phone, personnel_code)
    if match is not None:
        already_mine = (
            match.get("bale_id") == platform_id
            if platform == "bale"
            else match.get("telegram_id") == platform_id
        )
        if not already_mine:
            if platform == "telegram" and match["telegram_id"] != platform_id:
                # رکورد اصلی از قبل با کلید دیگری ثبت شده (مثلاً ثبت اولیه در بله)
                # — ابتدا تکراری‌ها آزاد و داده‌های همه به کلید جدید منتقل می‌شود
                deactivate_duplicate_accounts(platform_id, match["id"], future_owner_key=platform_id)
                _reassign_observations_owner(match["telegram_id"], platform_id)
                link_platform_account(match["id"], platform_id, platform)
            else:
                deactivate_duplicate_accounts(platform_id, match["id"])
                link_platform_account(match["id"], platform_id, platform)
            return match["id"], True
        return match["id"], False
    uid = add_user(
        telegram_id=platform_id,
        full_name=full_name,
        phone=phone,
        personnel_code=personnel_code,
        project_name=project_name,
        position=position,
        bale_id=platform_id if platform == "bale" else None,
    )
    return uid, False


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


def list_knowledge_by_user(telegram_id: int, active_only: bool = True) -> list[dict]:
    """تمام دانش‌های یک کاربر (جدیدترین اول). ورودی: شناسهٔ هر پلتفرم."""
    user = get_user_by_platform_id(telegram_id)
    if user is None:
        return []
    sql = "SELECT * FROM knowledge_entries WHERE reported_by = ?"
    if active_only:
        sql += " AND is_active = 1"
    sql += " ORDER BY id DESC"
    with get_connection() as conn:
        rows = conn.execute(sql, (user["id"],)).fetchall()
        return [_deserialize_knowledge(r) for r in rows]


def search_knowledge_by_user(
    telegram_id: int,
    keyword: str,
    limit: int = 30,
) -> list[dict]:
    """
    جستجوی متنی در دانش‌های خود کاربر.
    جستجو در: kn_number، عنوان/فیلدها (fields_json)، draft_text و raw_description.
    ورودی شناسه: هر پلتفرم.
    """
    user = get_user_by_platform_id(telegram_id)
    if user is None or not (keyword or "").strip():
        return []
    like = f"%{keyword.strip()}%"
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM knowledge_entries
               WHERE reported_by = ? AND is_active = 1
                 AND (kn_number LIKE ? OR fields_json LIKE ?
                      OR draft_text LIKE ? OR raw_description LIKE ?)
               ORDER BY id DESC LIMIT ?""",
            (user["id"], like, like, like, like, limit),
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
    """پیش‌نویس فعال (draft) کاربر را پیدا می‌کند. ورودی: شناسهٔ هر پلتفرم."""
    user = get_user_by_platform_id(telegram_id)
    if user is None:
        return None
    with get_connection() as conn:
        row = conn.execute(
            """SELECT ke.* FROM knowledge_entries ke
               WHERE ke.reported_by = ? AND ke.status = 'draft' AND ke.is_active = 1
               ORDER BY ke.id DESC LIMIT 1""",
            (user["id"],),
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
    title: str | None = None,
    obs_date: str | None = None,
) -> int:
    """یک مشاهدهٔ جدید ثبت می‌کند (وضعیت: raw). ورودی: شناسهٔ هر پلتفرم."""
    now = _now_str()
    user = get_user_by_platform_id(telegram_id)
    if user is None:
        raise ValueError(f"کاربر با شناسهٔ پلتفرم={telegram_id} یافت نشد.")
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO observations (telegram_id, title, content, status,
               project_name, tags, obs_date, created_at, updated_at)
               VALUES (?, ?, ?, 'raw', ?, ?, ?, ?, ?)""",
            (user["telegram_id"], title, content, project_name,
             json.dumps(tags or [], ensure_ascii=False), obs_date, now, now),
        )
        conn.commit()
        return cur.lastrowid


def list_observations_by_user(telegram_id: int, status: str | None = None) -> list[dict]:
    """مشاهدات یک کاربر (جدیدترین اول). ورودی: شناسهٔ هر پلتفرم."""
    user = get_user_by_platform_id(telegram_id)
    if user is None:
        return []
    owner_key = user["telegram_id"]
    with get_connection() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM observations WHERE telegram_id = ? AND status = ? ORDER BY id DESC",
                (owner_key, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM observations WHERE telegram_id = ? ORDER BY id DESC",
                (owner_key,),
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
                       tags: list[str] | None = None,
                       title: str | None = None,
                       obs_date: str | None = None) -> None:
    """به‌روزرسانی محتوای مشاهده."""
    now = _now_str()
    with get_connection() as conn:
        if content is not None:
            conn.execute(
                "UPDATE observations SET content = ?, updated_at = ? WHERE id = ?",
                (content, now, obs_id),
            )
        if tags is not None:
            conn.execute(
                "UPDATE observations SET tags = ?, updated_at = ? WHERE id = ?",
                (json.dumps(tags, ensure_ascii=False), now, obs_id),
            )
        if title is not None:
            conn.execute(
                "UPDATE observations SET title = ?, updated_at = ? WHERE id = ?",
                (title, now, obs_id),
            )
        if obs_date is not None:
            conn.execute(
                "UPDATE observations SET obs_date = ?, updated_at = ? WHERE id = ?",
                (obs_date, now, obs_id),
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


def search_observations(
    telegram_id: int,
    keyword: str | None = None,
    hashtag: str | None = None,
    obs_date: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """
    جستجو در مشاهدات کاربر.
    - ورودی: شناسهٔ هر پلتفرم (تلگرام یا بله)
    - keyword: جستجو در title و content
    - hashtag: جستجوی هشتگ (بدون #)
    - obs_date: تاریخ میلادی 'YYYY-MM-DD' یا 'YYYY-MM' (جستجوی ماه)
    """
    user = get_user_by_platform_id(telegram_id)
    if user is None:
        return []
    with get_connection() as conn:
        sql = "SELECT * FROM observations WHERE telegram_id = ?"
        params: list = [user["telegram_id"]]

        if keyword:
            sql += " AND (title LIKE ? OR content LIKE ?)"
            like = f"%{keyword}%"
            params += [like, like]
        if hashtag:
            sql += " AND tags LIKE ?"
            params.append(f"%{hashtag}%")
        if obs_date:
            if len(obs_date) == 7:  # YYYY-MM → جستجوی ماه
                sql += " AND obs_date LIKE ?"
                params.append(f"{obs_date}%")
            else:
                sql += " AND obs_date = ?"
                params.append(obs_date)

        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return _rows_to_dicts(rows)


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


def get_observation_attachment(attachment_id: int) -> dict | None:
    """یک پیوست مشاهده را با شناسه برمی‌گرداند."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM observation_attachments WHERE id = ?",
            (attachment_id,),
        ).fetchone()
        return _row_to_dict(row)


def remove_observation_attachment(attachment_id: int) -> None:
    """یک پیوست را حذف می‌کند."""
    with get_connection() as conn:
        conn.execute("DELETE FROM observation_attachments WHERE id = ?", (attachment_id,))
        conn.commit()