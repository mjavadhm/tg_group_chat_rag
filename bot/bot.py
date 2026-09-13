from __future__ import annotations

import asyncio
import logging
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramMigrateToChat
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    ErrorEvent,
)
from rich.console import Console

from bot.config import bot_settings
from bot.error_logger import report_error
from bot.handlers.admin import admin_router
from bot.handlers.user import user_router
from bot.thread_manager import ThreadTrackerMiddleware

console = Console()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | bot | %(levelname)s | %(message)s")
logger = logging.getLogger("bot")



async def setup_bot_commands(bot: Bot) -> None:
    """تنظیم منوی دستورات تلگرام با تفکیک دسترسی کاربران عادی و ادمین‌ها."""
    # منوی پیش‌فرض عمومی (برای همه کاربران عادی) - دستورات ادمین مخفی هستند
    public_commands = [
        BotCommand(command="start", description="شروع گفتگو و راهنما"),
        BotCommand(command="help", description="راهنمای نحوه پرسش"),
    ]
    await bot.set_my_commands(public_commands, scope=BotCommandScopeDefault())

    # منوی مخصوص ادمین‌ها (فقط در چت خصوصی ادمین‌های مشخص‌شده در ADMIN_IDS نمایش داده می‌شود)
    admin_commands = [
        BotCommand(command="start", description="شروع گفتگو"),
        BotCommand(command="help", description="راهنمای پرسش"),
        BotCommand(command="config", description="داشبورد تنظیمات ادمین"),
        BotCommand(command="stats", description="آمار پیام‌های دیتابیس"),
    ]
    for admin_id in bot_settings.admin_ids:
        try:
            await bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception as e:
            logger.debug(f"Could not set admin commands for user {admin_id}: {e}")


async def start_bot() -> None:
    """راه‌اندازی موتور ربات تلگرام با aiogram 3."""
    if not bot_settings.bot_token:
        console.print("[bold red]خطا:[/bold red] توکن ربات (BOT_TOKEN) در فایل .env تنظیم نشده است.")
        sys.exit(1)

    bot = Bot(
        token=bot_settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    # ثبت پیام‌های کل چت برای ردیابی دقیق تردهای ریپلای حتی پیش از منشن شدن ربات
    dp.message.outer_middleware(ThreadTrackerMiddleware())

    # اتصال روترها (ادمین اولویت بالاتری دارد)
    dp.include_router(admin_router)
    dp.include_router(user_router)

    # هندلر جامع ثبت خطاها (Global Error Handler)
    @dp.error()
    async def global_error_handler(event: ErrorEvent):
        exception = event.exception
        update = event.update

        if isinstance(exception, TelegramMigrateToChat):
            logger.warning(
                f"گروه به سوپرگروه با شناسه جدید {exception.migrate_to_chat_id} ارتقا یافت."
            )
            await report_error(
                bot,
                exception,
                update=update,
                context_note=f"ارتقای گروه تلگرام به سوپرگروه جدید: {exception.migrate_to_chat_id}",
            )
            return True

        logger.error(f"Global unhandled error: {type(exception).__name__}: {exception}", exc_info=True)
        await report_error(
            bot,
            exception,
            update=update,
            context_note="خطای هندل‌نشده در پایپ‌لاین تلگرام",
        )
        return True

    me = await bot.get_me()
    console.print(f"[bold green]✓ ربات با موفقیت فعال شد:[/bold green] @{me.username} (ID: {me.id})")
    console.print(f"[cyan]ادمین‌های مجاز:[/cyan] {bot_settings.admin_ids or 'هیچ ادمینی در ADMIN_IDS تعریف نشده'}")
    console.print(f"[magenta]مدل فعال:[/magenta] {bot_settings.llm_model} | [dim]{bot_settings.llm_base_url}[/dim]")
    console.print(f"[blue]تعداد منابع بازیابی RAG:[/blue] {bot_settings.rag_top_k}")
    if bot_settings.error_channel_id:
        console.print(f"[red]کانال ثبت خطاها:[/red] {bot_settings.error_channel_id}")

    await setup_bot_commands(bot)

    console.print("[green]در حال گوش دادن به پیام‌ها (Polling)...[/green]")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(start_bot())
