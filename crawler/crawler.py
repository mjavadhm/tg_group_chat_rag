from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rich.console import Console
from rich.table import Table
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.types import Channel, Chat

from core.config import settings
from core.db import get_db, init_db
from core.repo import get_crawl_state, update_crawl_state, upsert_messages_batch
from crawler.extractor import extract_message_record

console = Console()
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("crawler")


async def get_client() -> TelegramClient:
    """ساخت و مقداردهی کلاینت Telethon."""
    if not settings.api_id or not settings.api_hash:
        raise ValueError(
            "لطفاً مقادیر TELEGRAM_API_ID و TELEGRAM_API_HASH را در فایل .env وارد کنید."
        )

    client = TelegramClient(
        settings.session_path,
        settings.api_id,
        settings.api_hash,
    )
    return client


async def resolve_target(client: TelegramClient, target: str | int):
    """شناسایی گروه تلگرام از طریق آیدی عددی، لینک یا نام کاربری."""
    try:
        # اگر آیدی به صورت عددی ارسال شده باشد
        if isinstance(target, str) and (target.startswith("-100") or target.lstrip("-").isdigit()):
            target = int(target)
        entity = await client.get_entity(target)
        return entity
    except Exception as e:
        console.print(f"[bold red]خطا در یافتن گروه هدف '{target}':[/bold red] {e}")
        raise


async def run_crawler(target_chat: str | None = None, limit: int | None = None, resume: bool = True):
    """
    اجرای کراولر استخراج تاریخچه پیام‌ها:
    - target_chat: شناسه یا یوزرنیم گروه (اگر خالی باشد از .env خوانده می‌شود)
    - limit: حداکثر تعداد پیام برای کراول (None یعنی تمام تاریخچه تا پیام اول)
    - resume: ادامه از آخرین پیام ذخیره شده در دیتابیس
    """
    target = target_chat or settings.target_chat
    if not target:
        raise ValueError("شناسه گروه هدف مشخص نشده است (در .env یا ورودی دستور).")

    init_db()
    client = await get_client()

    console.print("[cyan]در حال اتصال به تلگرام...[/cyan]")
    await client.start(phone=settings.phone if settings.phone else None)
    console.print("[bold green]✓ با موفقیت به اکانت تلگرام متصل شد.[/bold green]")

    entity = await resolve_target(client, target)
    chat_id = entity.id
    chat_title = getattr(entity, "title", str(chat_id))
    chat_username = getattr(entity, "username", None)

    console.print(
        f"[bold yellow]گروه هدف شناسایی شد:[/bold yellow] [bold white]{chat_title}[/bold white] "
        f"(ID: [cyan]{chat_id}[/cyan], Username: @{chat_username or 'ندارد'})"
    )

    with get_db() as conn:
        state = get_crawl_state(conn, chat_id)
        last_oldest_id = state["oldest_msg_id"] if state else None
        last_newest_id = state["newest_msg_id"] if state else None
        total_already = state["total_crawled"] if state else 0

        console.print(
            f"[dim]وضعیت قبلی: {total_already} پیام ثبت شده | "
            f"قدیمی‌ترین ID: {last_oldest_id} | جدیدترین ID: {last_newest_id}[/dim]"
        )

        update_crawl_state(
            conn,
            chat_id=chat_id,
            chat_title=chat_title,
            chat_username=chat_username,
            status="running",
        )

    # پیمایش تاریخچه پیام‌ها
    batch: list[dict[str, Any]] = []
    total_saved = 0
    min_id_seen = last_oldest_id if (resume and last_oldest_id) else None
    max_id_seen = last_newest_id if (resume and last_newest_id) else None

    # اگر از قبل قدیمی‌ترین پیام ثبت شده باشد و حالت resume فعال باشد،
    # می‌توانیم از همان پیام به سمت پیام‌های قدیمی‌تر برویم (offset_id = last_oldest_id)
    offset_id = (last_oldest_id if (resume and last_oldest_id) else 0) or 0

    console.print(
        f"[bold green]شروع دریافت پیام‌ها...[/bold green] "
        f"(نقطه شروع offset_id: {offset_id if offset_id > 0 else 'جدیدترین پیام'})"
    )

    try:
        async for message in client.iter_messages(
            entity,
            offset_id=offset_id,
            limit=limit,
            wait_time=settings.delay_seconds,
        ):
            try:
                record = extract_message_record(message, chat_id)
            except Exception as e:
                console.print(f"[yellow]هشدار در پردازش پیام {getattr(message, 'id', '?')}: {e}[/yellow]")
                record = None

            if record is not None:
                batch.append(record)
                msg_id = record["message_id"]

                min_id_seen = msg_id if min_id_seen is None else min(min_id_seen, msg_id)
                max_id_seen = msg_id if max_id_seen is None else max(max_id_seen, msg_id)

            # ذخیره دوره‌ای دسته‌ای در دیتابیس
            if len(batch) >= settings.batch_size:
                with get_db() as conn:
                    saved_count = upsert_messages_batch(conn, batch)
                    update_crawl_state(
                        conn,
                        chat_id=chat_id,
                        oldest_msg_id=min_id_seen,
                        newest_msg_id=max_id_seen,
                        total_increment=len(batch),
                        status="running",
                    )
                total_saved += len(batch)
                console.print(
                    f"✓ ذخیره شد: [bold cyan]{len(batch)}[/bold cyan] پیام "
                    f"(مجموع این نشست: [bold green]{total_saved}[/bold green] | آخرین Message ID: {min_id_seen})"
                )
                batch.clear()

        # ذخیره باقی‌مانده پیام‌های بچ آخر
        if batch:
            with get_db() as conn:
                upsert_messages_batch(conn, batch)
                update_crawl_state(
                    conn,
                    chat_id=chat_id,
                    oldest_msg_id=min_id_seen,
                    newest_msg_id=max_id_seen,
                    total_increment=len(batch),
                    status="completed" if not limit else "idle",
                )
            total_saved += len(batch)
            console.print(f"✓ ذخیره شد: [bold cyan]{len(batch)}[/bold cyan] پیام نهایی.")
            batch.clear()

        console.print(
            f"\n[bold green]کراول با موفقیت انجام شد![/bold green] "
            f"مجموع پیام‌های جدید ذخیره شده: [bold yellow]{total_saved}[/bold yellow]"
        )

    except FloodWaitError as e:
        console.print(f"[bold red]محدودیت نرخ تلگرام (FloodWait):[/bold red] باید {e.seconds} ثانیه صبر کنیم...")
        if batch:
            with get_db() as conn:
                upsert_messages_batch(conn, batch)
                update_crawl_state(
                    conn,
                    chat_id=chat_id,
                    oldest_msg_id=min_id_seen,
                    newest_msg_id=max_id_seen,
                    total_increment=len(batch),
                    status="paused_flood",
                )
        await asyncio.sleep(e.seconds + 2)
    except KeyboardInterrupt:
        console.print("\n[yellow]کراول توسط کاربر متوقف شد. در حال ذخیره آخرین پیشرفت...[/yellow]")
        if batch:
            with get_db() as conn:
                upsert_messages_batch(conn, batch)
                update_crawl_state(
                    conn,
                    chat_id=chat_id,
                    oldest_msg_id=min_id_seen,
                    newest_msg_id=max_id_seen,
                    total_increment=len(batch),
                    status="interrupted",
                )
        console.print("[bold green]پیشرفت ذخیره شد. در اجرای بعدی از همین نقطه ادامه داده می‌شود.[/bold green]")
    finally:
        await client.disconnect()
