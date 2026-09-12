from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from core.config import settings

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def ensure_data_dir() -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)


def connect() -> sqlite3.Connection:
    """اتصال به SQLite با تنظیمات پیشرفته همزمانی (WAL Mode) برای جلوگیری از قفل شدن دیتابیس."""
    ensure_data_dir()
    conn = sqlite3.connect(str(settings.db_path), timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """اجرای اسکریپت اولیه ایجاد جداول و ایندکس‌ها."""
    ensure_data_dir()
    with get_db() as conn:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            schema_sql = f.read()
        conn.executescript(schema_sql)
