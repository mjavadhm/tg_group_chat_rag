from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from dotenv import load_dotenv

# لود آنی متغیرهای محیطی از .env در ابتدای اجرای برنامه
load_dotenv(Path(__file__).resolve().parent / ".env")

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


from rich.console import Console
from rich.table import Table

from core.db import get_db, init_db
from core.repo import get_stats
from crawler.crawler import run_crawler

console = Console()


def show_stats():
    """نمایش گزارش آماری کامل از داده‌های استخراج‌شده در دیتابیس."""
    init_db()
    with get_db() as conn:
        stats = get_stats(conn)

    table = Table(title="📊 خلاصه آمار پیام‌های ذخیره‌شده در دیتابیس", style="cyan")
    table.add_column("شاخص", style="bold white")
    table.add_column("مقدار", style="bold yellow")

    table.add_row("تعداد کل پیام‌ها", f"{stats['total_messages']:,}")
    table.add_row("تعداد کاربران فرستنده", f"{stats['total_senders']:,}")
    table.add_row("تعداد پیام‌های ریپلای‌شده", f"{stats['total_replies']:,}")
    table.add_row("قدیمی‌ترین پیام ثبت‌شده", str(stats['first_message_date'] or "-"))
    table.add_row("جدیدترین پیام ثبت‌شده", str(stats['last_message_date'] or "-"))

    console.print(table)

    # جدول تفکیک نوع محتوا و رسانه‌ها
    media_table = Table(title="📁 تفکیک نوع محتوا و رسانه‌ها (Media Breakdown)", style="magenta")
    media_table.add_column("نوع مدیا / محتوا", style="bold white")
    media_table.add_column("تعداد", style="bold green")

    media_names = {
        "text": "متن ساده (Text)",
        "photo": "تصویر (Photo)",
        "video": "ویدئو (Video)",
        "voice": "پیام صوتی / ویس (Voice)",
        "audio": "موزیک / پادکست (Audio)",
        "document": "فایل / سند (Document/PDF/Zip)",
        "animation": "گیف / انیمیشن (GIF)",
        "sticker": "استیکر (Sticker)",
        "poll": "نظرسنجی (Poll)",
        "contact": "مخاطب (Contact)",
        "location": "موقعیت مکانی (Location)",
        "web_page": "پیش‌نمایش لینک (Web Link)",
    }

    for mtype, count in stats["media_breakdown"].items():
        media_table.add_row(media_names.get(mtype, mtype), f"{count:,}")

    console.print(media_table)


def show_recent(limit: int = 15):
    """نمایش آخرین رکوردهای ذخیره شده برای راستی‌آزمایی صحت داده‌ها."""
    init_db()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT message_id, sender_name, media_type, reply_to_msg_id, text, date
            FROM messages
            ORDER BY date DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cursor.fetchall()

    if not rows:
        console.print("[yellow]هنوز پیامی در دیتابیس ذخیره نشده است.[/yellow]")
        return

    table = Table(title=f"🕒 {limit} پیام اخیر استخراج‌شده", style="blue")
    table.add_column("Msg ID", style="cyan", width=10)
    table.add_column("فرستنده", style="green", width=18)
    table.add_column("مدیا", style="magenta", width=10)
    table.add_column("ریپلای به", style="yellow", width=10)
    table.add_column("متن / کپشن", style="white")

    for r in rows:
        text_preview = (r["text"] or "").strip().replace("\n", " ")
        if len(text_preview) > 50:
            text_preview = text_preview[:47] + "..."
        table.add_row(
            str(r["message_id"]),
            str(r["sender_name"] or "نامشخص"),
            str(r["media_type"] or "متن"),
            str(r["reply_to_msg_id"] or "-"),
            text_preview or "[dim]-[/dim]",
        )

    console.print(table)


def main():
    parser = argparse.ArgumentParser(description="ربات استخراج تاریخچه گروه تلگرام برای پایگاه RAG")
    subparsers = parser.add_subparsers(dest="command", help="دستور مورد نظر")

    # دستور init
    subparsers.add_parser("init", help="ایجاد اولیه جداول دیتابیس SQLite")

    # دستور crawl
    crawl_parser = subparsers.add_parser("crawl", help="شروع دریافت پیام‌های گروه تلگرام")
    crawl_parser.add_argument("--target", "-t", type=str, default=None, help="شناسه یا یوزرنیم گروه (مثلاً @group)")
    crawl_parser.add_argument("--limit", "-l", type=int, default=None, help="حداکثر تعداد پیام برای دانلود")
    crawl_parser.add_argument("--no-resume", action="store_true", help="شروع کراول از ابتدا بدون توجه به آخرین رکورد")

    # دستور classify
    subparsers.add_parser("classify", help="دسته‌بندی و برچسب‌گذاری پیام‌ها بدون حذف داده")

    # دستور threads
    thread_parser = subparsers.add_parser("threads", help="ساخت تردهای گفتگو همراه با تگ‌های رفرنس [msg:ID] برای RAG")
    thread_parser.add_argument("--output", "-o", type=str, default="data/threads.jsonl", help="مسیر ذخیره فایل خروجی JSONL")

    # دستور bot
    subparsers.add_parser("bot", help="راه‌اندازی ربات پاسخگوی هوشمند تلگرام با موتور RAG")

    # دستور stats
    subparsers.add_parser("stats", help="مشاهده آمار داده‌های ذخیره‌شده")

    # دستور recent
    recent_parser = subparsers.add_parser("recent", help="مشاهده آخرین پیام‌های استخراج‌شده")
    recent_parser.add_argument("--limit", "-n", type=int, default=15, help="تعداد رکوردهای نمایشی")

    # دستور migrate-qdrant
    mig_parser = subparsers.add_parser("migrate-qdrant", help="انتقال وکتورهای محلی به Qdrant Cloud یا سرور مجزا")
    mig_parser.add_argument("--url", type=str, default=None, help="آدرس کلاستر ابری Qdrant")
    mig_parser.add_argument("--api-key", type=str, default=None, help="کلید دسترسی API")
    mig_parser.add_argument("--batch-size", "-b", type=int, default=50, help="تعداد وکتورها در هر درخواست (پیش‌فرض: ۵۰)")

    args = parser.parse_args()

    if args.command == "init":
        init_db()
        console.print("[bold green]✓ ساختار جداول SQLite با موفقیت آماده شد.[/bold green]")
    elif args.command == "classify":
        from processor.classifier import print_classification_report, run_classification
        res = run_classification()
        print_classification_report(res)
    elif args.command == "threads":
        from processor.threader import export_threads_to_jsonl
        export_threads_to_jsonl(output_path=args.output)
    elif args.command == "bot":
        from bot.bot import start_bot
        asyncio.run(start_bot())
    elif args.command == "crawl":
        asyncio.run(
            run_crawler(
                target_chat=args.target,
                limit=args.limit,
                resume=not args.no_resume,
            )
        )
    elif args.command == "stats":
        show_stats()
    elif args.command == "recent":
        show_recent(limit=args.limit)
    elif args.command == "migrate-qdrant":
        from vector_store.migrate_to_cloud import migrate
        migrate(url=args.url, api_key=args.api_key, batch_size=args.batch_size)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
