# -*- coding: utf-8 -*-
"""
بکاپ روزانه دیتابیس MAPNAMD1-knowledge.

- از SQLite Online Backup API استفاده می‌کند (امن حتی وقتی ربات‌ها در حال نوشتن هستند)
- خروجی: فایل فشرده knowledge-YYYY-MM-DD.db.gz در پوشهٔ بکاپ (بیرون از ریپو)
- نگهداری: آخرین KEEP_DAILY نسخهٔ روزانه؛ قدیمی‌ترها خودکار حذف می‌شوند
- یکپارچگی فایل بکاپ با PRAGMA integrity_check بررسی می‌شود

زمان‌بندی (cron روی سرور):
    30 3 * * * cd /root/MAPNAMD1-knowledge && .venv/bin/python scripts/backup_db.py >> logs/backup.log 2>&1

بازیابی دستی:
    gunzip -c /root/backups/knowledgebot/knowledge-YYYY-MM-DD.db.gz > data/knowledge.db
    (قبل از بازیابی، سرویس‌ها متوقف شوند: systemctl stop knowledgebot-bale knowledgebot)

موارد آینده (هنوز پیاده نشده):
    - ارسال نسخهٔ بکاپ خارج از سرور (چت ادمین در بله/تلگرام یا object storage)
    - بکاپ دوره‌ای پوشهٔ media/ (عکس‌ها و PDF ها)
"""

from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import DB_PATH  # noqa: E402

BACKUP_DIR = Path(os.environ.get("KNOWLEDGE_BACKUP_DIR", "/root/backups/knowledgebot"))
KEEP_DAILY = int(os.environ.get("KNOWLEDGE_BACKUP_KEEP", "7"))


def create_backup() -> Path:
    """یک نسخهٔ بکاپ کامل و فشرده از دیتابیس می‌سازد و مسیرش را برمی‌گرداند."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    gz_path = BACKUP_DIR / f"knowledge-{stamp}.db.gz"
    tmp_path = BACKUP_DIR / f"knowledge-{stamp}.db.tmp"

    # بکاپ آنلاین — امن در حین فعالیت ربات‌ها
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(tmp_path)
    try:
        with dst:
            src.backup(dst)
        check = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise RuntimeError(f"integrity_check بکاپ ناموفق بود: {check}")
    finally:
        dst.close()
        src.close()

    with open(tmp_path, "rb") as f_in, gzip.open(gz_path, "wb", compresslevel=9) as f_out:
        shutil.copyfileobj(f_in, f_out)
    tmp_path.unlink()
    return gz_path


def prune_old_backups() -> int:
    """نسخه‌های قدیمی‌تر از KEEP_DAILY روز را حذف می‌کند. خروجی: تعداد حذف‌شده."""
    cutoff = time.time() - KEEP_DAILY * 86400
    removed = 0
    for p in BACKUP_DIR.glob("knowledge-*.db.gz"):
        if p.stat().st_mtime < cutoff:
            p.unlink()
            removed += 1
    return removed


def main() -> None:
    if not Path(DB_PATH).exists():
        print(f"❌ دیتابیس یافت نشد: {DB_PATH}", file=sys.stderr)
        sys.exit(1)
    gz = create_backup()
    removed = prune_old_backups()
    size_kb = gz.stat().st_size // 1024
    print(f"✅ بکاپ شد: {gz} ({size_kb}KB) | حذف نسخه‌های قدیمی: {removed}")


if __name__ == "__main__":
    main()
