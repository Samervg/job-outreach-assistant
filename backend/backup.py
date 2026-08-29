import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from backend.config import BACKUP_DIR, DATABASE_PATH


def create_database_backup(
    source_path: Path = DATABASE_PATH,
    backup_dir: Path = BACKUP_DIR,
) -> Path:
    """Create an explicit, SQLite-consistent timestamped database backup."""
    source_path = Path(source_path)
    if not source_path.is_file():
        raise FileNotFoundError("Yedeklenecek SQLite veritabanı bulunamadı.")

    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = backup_dir / f"job_outreach_{timestamp}.db"

    source = None
    target = None
    failed = False
    try:
        source = sqlite3.connect(source_path, timeout=30)
        target = sqlite3.connect(destination, timeout=30)
        source.execute("PRAGMA busy_timeout = 30000")
        source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise sqlite3.DatabaseError("Oluşturulan veritabanı yedeği geçersiz.")
    except Exception:
        failed = True
        raise
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()
        if failed:
            destination.unlink(missing_ok=True)
    return destination


if __name__ == "__main__":
    print(create_database_backup())
