from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

# بارگذاری خودکار فایل .env از ریشه پروژه
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    # مشخصات اکانت تلگرام (از my.telegram.org)
    api_id: int = int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash: str = os.getenv("TELEGRAM_API_HASH", "")
    phone: str = os.getenv("TELEGRAM_PHONE", "")
    
    # شناسه یا یوزرنیم گروه هدف (مثلاً @group_username یا -100123456789)
    target_chat: str = os.getenv("TARGET_CHAT", "")
    
    # مسیر ذخیره‌سازی داده‌ها
    db_path: Path = BASE_DIR / os.getenv("DB_PATH", "data/messages.db")
    session_dir: Path = BASE_DIR / os.getenv("SESSION_DIR", "sessions")
    session_name: str = os.getenv("SESSION_NAME", "telegram_crawler")
    
    # تنظیمات کراولر و نرخ درخواست‌ها
    batch_size: int = int(os.getenv("BATCH_SIZE", "100"))
    delay_seconds: float = float(os.getenv("DELAY_SECONDS", "1.0"))
    checkpoint_every: int = int(os.getenv("CHECKPOINT_EVERY", "50"))

    @property
    def session_path(self) -> str:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        return str(self.session_dir / self.session_name)


settings = Settings()
