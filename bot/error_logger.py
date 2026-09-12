from __future__ import annotations

import datetime
import html
import logging
import traceback
from typing import Any
from aiogram import Bot
from aiogram.types import Message, Update

from bot.config import bot_settings

logger = logging.getLogger("bot.error_logger")


def _format_datetime() -> str:
    """فرمت‌بندی زمان دقیق وقوع خطا به وقت محلی."""
    now = datetime.datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S")


def _truncate_text(text: str, max_len: int = 1500) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "... [بقیه ترنسکریپت کوتاه شد]"


async def report_error(
    bot: Bot,
    exception: Exception,
    message: Message | None = None,
    update: Update | None = None,
    context_note: str = "",
) -> None:
    """
    ارسال گزارش تفصیلی خطا به کانال لاگ تلگرام (ERROR_CHANNEL_ID).
    این تابع شامل زمان دقیق، مشخصات پیام، کاربر، چت و متن کامل Traceback است.
    """
    target_channel = bot_settings.error_channel_id

    # لاگ درون کنسول
    logger.error(f"Handled Exception: {type(exception).__name__}: {exception}", exc_info=True)

    if not target_channel:
        # اگر کانالی ست نشده باشد، لاگ فقط در کنسول می‌ماند
        return

    time_str = _format_datetime()
    err_type = type(exception).__name__
    err_msg = html.escape(str(exception) or "بدون پیام خطا")

    # استخراج اطلاعات پیام
    msg_obj = message
    if not msg_obj and update and update.message:
        msg_obj = update.message

    chat_info = "نامشخص"
    user_info = "نامشخص"
    msg_info = "ندارد"
    msg_text = ""

    if msg_obj:
        chat = msg_obj.chat
        chat_title = chat.title or chat.username or "بدون نام"
        chat_info = f"<code>{chat.id}</code> ({html.escape(chat_title)} | {chat.type})"

        if msg_obj.from_user:
            u = msg_obj.from_user
            full_name = html.escape(f"{u.first_name or ''} {u.last_name or ''}".strip())
            uname = f"@{u.username}" if u.username else "ندارد"
            user_info = f"<code>{u.id}</code> ({full_name} | {uname})"

        msg_info = f"#{msg_obj.message_id}"
        if msg_obj.text:
            msg_text = html.escape(_truncate_text(msg_obj.text, max_len=300))

    # قالب‌بندی Traceback
    tb_str = "".join(traceback.format_exception(type(exception), exception, exception.__traceback__))
    tb_clean = html.escape(_truncate_text(tb_str, max_len=2000))

    report_lines = [
        f"🚨 <b>گزارش خطای سیستمی ربات</b>",
        f"⏱ <b>زمان:</b> <code>{time_str}</code>",
        f"⚠️ <b>نوع خطا:</b> <code>{html.escape(err_type)}</code>",
        f"💬 <b>چت:</b> {chat_info}",
        f"👤 <b>کاربر:</b> {user_info}",
        f"📩 <b>پیام:</b> {msg_info}",
    ]

    if context_note:
        report_lines.append(f"📌 <b>توضیح زمینه:</b> {html.escape(context_note)}")

    if msg_text:
        report_lines.append(f"📝 <b>متن پیام کاربر:</b>\n<blockquote>{msg_text}</blockquote>")

    report_lines.append(f"❌ <b>پیام ارور:</b>\n<code>{err_msg}</code>")
    report_lines.append(f"\n🔍 <b>ردیابی خطا (Traceback):</b>\n<pre><code class=\"language-python\">{tb_clean}</code></pre>")

    full_report = "\n".join(report_lines)

    # ارسال امن به کانال تلگرام
    try:
        await bot.send_message(
            chat_id=target_channel,
            text=full_report,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception as send_err:
        logger.warning(f"Failed to send error report to {target_channel}: {send_err}")
        # در صورت بروز خطای ارتقای گروه (TelegramMigrateToChat)، آیدی جدید لاگ می‌شود
        from aiogram.exceptions import TelegramMigrateToChat
        if isinstance(send_err, TelegramMigrateToChat):
            new_id = send_err.migrate_to_chat_id
            logger.info(f"Target error channel migrated to {new_id}. Retrying...")
            try:
                await bot.send_message(
                    chat_id=new_id,
                    text=full_report,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception:
                pass
