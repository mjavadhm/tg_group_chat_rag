from __future__ import annotations

import argparse
import os
import pickle
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn

console = Console()


def migrate(
    url: str | None = None,
    api_key: str | None = None,
    collection_name: str = "motor_threads",
    sqlite_path: Path | str = "data/qdrant_db/collection/motor_threads/storage.sqlite",
    batch_size: int = 50,
    skip_test_points: bool = True,
) -> bool:
    target_url = url or os.getenv("QDRANT_URL")
    target_api_key = api_key or os.getenv("QDRANT_API_KEY")

    if not target_url:
        console.print("[bold red]خطا:[/bold red] آدرس Qdrant Cloud (QDRANT_URL) تنظیم نشده است!")
        console.print("لطفاً آدرس کلاستر را در فایل .env قرار دهید یا با فلگ --url مشخص کنید.")
        return False

    console.print(f"[bold cyan]🚀 آغاز انتقال داده‌ها به پایگاه ابری Qdrant:[/bold cyan]")
    console.print(f"  • سرور مقصد: [yellow]{target_url}[/yellow]")
    console.print(f"  • نام کالکشن: [green]{collection_name}[/green]")
    console.print(f"  • حجم هر دسته (Batch Size): [magenta]{batch_size}[/magenta]")

    # ۱. اتصال به سرور ابری و تست ارتباط (با تایم‌اوت ۱۲۰ ثانیه برای اینترنت‌های ناپایدار و پراکسی)
    try:
        remote_client = QdrantClient(url=target_url, api_key=target_api_key, timeout=120.0)
        existing_colls = [c.name for c in remote_client.get_collections().collections]
        console.print(f"[green]✓ اتصال به Qdrant ابری با موفقیت برقرار شد.[/green]")
    except Exception as e:
        console.print(f"[bold red]✗ خطا در برقراری ارتباط با سرور ابری:[/bold red] {e}")
        return False

    # ۲. ایجاد کالکشن در صورت عدم وجود (ابعاد ۱۰۲۴ مدل BGE-M3 با متریک Cosine)
    if collection_name not in existing_colls:
        console.print(f"[yellow]در حال ایجاد کالکشن {collection_name} روی سرور ابری...[/yellow]")
        remote_client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
        )
        console.print(f"[bold green]✓ کالکشن {collection_name} با موفقیت در فضای ابری ایجاد شد.[/bold green]")
    else:
        info = remote_client.get_collection(collection_name)
        remote_count = info.points_count or 0
        console.print(f"[blue]ℹ کالکشن از قبل وجود دارد ({remote_count:,} رکورد فعلی). داده‌های جدید آپدیت می‌شوند.[/blue]")

    # ۳. بررسی فایل محلی storage.sqlite
    sqlite_file = BASE_DIR / sqlite_path
    if not sqlite_file.exists():
        console.print(f"[bold red]خطا:[/bold red] فایل لوکال {sqlite_file} پیدا نشد!")
        return False

    # باز کردن دیتابیس لوکال در حالت Read-Only بدون ایجاد قفل
    conn = sqlite3.connect(f"file:{sqlite_file}?mode=ro", uri=True)
    cursor = conn.cursor()
    cursor.execute("SELECT count(*) FROM points")
    total_points = cursor.fetchone()[0]

    console.print(f"[bold white]تعداد کل وکتورهای آماده انتقال:[/bold white] [bold yellow]{total_points:,}[/bold yellow]")

    if total_points == 0:
        console.print("[yellow]هیچ رکوردی برای انتقال یافت نشد.[/yellow]")
        return True

    # ۴. انتقال دسته‌ای (Batch) با نمایش درصد پیشرفت
    cursor.execute("SELECT point FROM points")

    batch = []
    transferred = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("• {task.completed}/{task.total} وکتور"),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]در حال آپلود وکتورها به ابر...", total=total_points)

        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break

            points_batch = []
            for row in rows:
                try:
                    pt = pickle.loads(row[0])
                    # اگر فیلتر فعال باشد، سوالات تستی و لایو ثبت‌شده در حین توسعه آپلود نمی‌شوند
                    if skip_test_points and str(pt.payload.get("thread_id", "")).startswith("live_"):
                        continue
                    points_batch.append(pt)
                except Exception as ex:
                    console.print(f"[dim red]خطا در خواندن رکورد: {ex}[/dim red]")

            if points_batch:
                import time
                for attempt in range(1, 6):
                    try:
                        remote_client.upsert(
                            collection_name=collection_name,
                            points=points_batch,
                            wait=False,
                        )
                        break
                    except Exception as err:
                        if attempt == 5:
                            console.print(f"\n[bold red]خطا پس از ۵ بار تلاش برای بچ {transferred}:[/bold red] {err}")
                            raise err
                        console.print(f"[dim yellow]تلاش مجدد بچ ({attempt}/5)...[/dim yellow]")
                        time.sleep(2 * attempt)

                transferred += len(points_batch)
                progress.update(task, advance=len(points_batch))

    conn.close()

    # ۵. راستی‌آزمایی نهایی
    final_info = remote_client.get_collection(collection_name)
    final_count = final_info.points_count or 0
    console.print(f"\n[bold green]🎉 انتقال با موفقیت کامل شد![/bold green]")
    console.print(f"  • تعداد وکتورهای موجود در کلاستر ابری: [bold yellow]{final_count:,}[/bold yellow]")
    console.print("[green]اکنون ربات می‌تواند بدون هیچ مصرف رمی به این پایگاه متصل شود.[/green]")
    return True


def main():
    parser = argparse.ArgumentParser(description="انتقال داده‌های وکتور لوکال Qdrant به سرور ابری (Qdrant Cloud)")
    parser.add_argument("--url", type=str, default=None, help="آدرس کلاستر ابری Qdrant")
    parser.add_argument("--api-key", type=str, default=None, help="کلید API دسترسی به کلاستر")
    parser.add_argument("--batch-size", type=int, default=250, help="تعداد وکتورها در هر درخواست آپلود (پیش‌فرض: 250)")
    parser.add_argument("--collection", type=str, default="motor_threads", help="نام کالکشن مقصد")
    args = parser.parse_args()

    migrate(
        url=args.url,
        api_key=args.api_key,
        collection_name=args.collection,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    main()
