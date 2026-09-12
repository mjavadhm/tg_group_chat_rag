from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

from rich.console import Console
from rich.table import Table

console = Console()


def clean_database(
    target_chat_id: int = 2073752206,
    db_path: Path | str = "data/messages.db",
    backup: bool = True,
) -> bool:
    full_path = BASE_DIR / db_path
    if not full_path.exists():
        console.print(f"[bold red]خطا:[/bold red] فایل دیتابیس {full_path} پیدا نشد!")
        return False

    console.print(f"[bold cyan]🧹 پاک‌سازی دیتابیس برای انتقال به سرور (شروع تازه ربات)[/bold cyan]")

    # ۱. تهیه نسخه پشتیبان
    if backup:
        bak_path = full_path.with_suffix(".db.bak")
        shutil.copy2(full_path, bak_path)
        console.print(f"[green]✓ نسخه پشتیبان در {bak_path.name} ذخیره شد.[/green]")

    conn = sqlite3.connect(str(full_path))
    cursor = conn.cursor()

    # ۲. آمار قبل از پاک‌سازی
    cursor.execute("SELECT chat_id, count(*) FROM messages GROUP BY chat_id")
    breakdown = cursor.fetchall()

    cursor.execute("SELECT count(*) FROM messages")
    total_before = cursor.fetchone()[0]

    table = Table(title="📊 وضعیت پیام‌ها قبل از پاک‌سازی", style="yellow")
    table.add_column("شناسه چت (Chat ID)", style="cyan")
    table.add_column("توضیحات", style="white")
    table.add_column("تعداد پیام‌ها", style="bold green")

    for cid, cnt in breakdown:
        desc = "گروه اصلی دومینار (آرشیو)" if cid == target_chat_id else "تست‌ها و تعاملات شخصی ربات"
        table.add_row(str(cid), desc, f"{cnt:,}")

    console.print(table)

    # ۳. حذف پیام‌های غیر از گروه اصلی
    cursor.execute("DELETE FROM messages WHERE chat_id != ?", (target_chat_id,))
    deleted_count = cursor.rowcount
    conn.commit()

    # ۴. بهینه‌سازی دیسک با VACUUM
    cursor.execute("VACUUM")
    conn.commit()

    cursor.execute("SELECT count(*) FROM messages")
    total_after = cursor.fetchone()[0]
    conn.close()

    console.print(f"\n[bold green]✓ پاک‌سازی با موفقیت انجام شد:[/bold green]")
    console.print(f"  • تعداد پیام‌های تستی حذف‌شده: [bold red]{deleted_count:,}[/bold red]")
    console.print(f"  • تعداد پیام‌های باقیمانده (فقط گروه اصلی): [bold yellow]{total_after:,}[/bold yellow]")
    console.print("[cyan]دیتابیس اکنون کاملاً تمیز و آماده انتقال به سرور است.[/cyan]")
    return True


if __name__ == "__main__":
    clean_database()
