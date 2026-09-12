from __future__ import annotations

import collections
import logging
from typing import Any
from aiogram.types import Message

from core.db import get_db

logger = logging.getLogger("bot.thread_manager")

# نگهداری کش درون‌حافظه‌ای ۵۰۰ پیام اخیر چت برای ردیابی آنی تردهای ریپلای
_RECENT_CACHE: collections.OrderedDict[int, dict[str, Any]] = collections.OrderedDict()
MAX_CACHE_SIZE = 1000


def record_message(message: Message) -> None:
    """
    ثبت فراداده هر پیام دریافتی در کش حافظه و دیتابیس جهت ردیابی زنجیره ریپلای‌ها.
    این تابع روی تک‌تک پیام‌های گروه در پس‌زمینه اجرا می‌شود.
    """
    if not message or not message.message_id:
        return

    sender_name = "کاربر"
    sender_id = None
    is_bot = False

    if message.from_user:
        u = message.from_user
        sender_name = u.full_name or u.username or "کاربر"
        sender_id = u.id
        is_bot = u.is_bot

    reply_to_id = message.reply_to_message.message_id if message.reply_to_message else None
    text_content = message.text or message.caption or ""

    entry = {
        "message_id": message.message_id,
        "chat_id": message.chat.id,
        "sender_name": sender_name,
        "sender_id": sender_id,
        "is_bot": is_bot,
        "text": text_content,
        "reply_to_msg_id": reply_to_id,
    }

    _RECENT_CACHE[message.message_id] = entry
    if len(_RECENT_CACHE) > MAX_CACHE_SIZE:
        _RECENT_CACHE.popitem(last=False)


def get_message_info(message_id: int, chat_id: int | None = None) -> dict[str, Any] | None:
    """واکشی مشخصات یک پیام از کش حافظه یا دیتابیس SQLite."""
    if message_id in _RECENT_CACHE:
        return _RECENT_CACHE[message_id]

    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT message_id, chat_id, sender_name, sender_id, text, reply_to_msg_id FROM messages WHERE message_id = ?",
                (message_id,)
            ).fetchone()
            if row:
                return {
                    "message_id": row["message_id"],
                    "chat_id": row["chat_id"],
                    "sender_name": row["sender_name"] or "کاربر",
                    "sender_id": row["sender_id"],
                    "is_bot": False,
                    "text": row["text"] or "",
                    "reply_to_msg_id": row["reply_to_msg_id"],
                }
    except Exception as e:
        logger.debug(f"Error fetching message #{message_id} from sqlite: {e}")

    return None


def extract_thread_context(
    message: Message,
    bot_id: int,
    max_depth: int = 20,
) -> list[dict[str, str]]:
    """
    استخراج دقیق زنجیره ریپلای (Thread) برای ارسال به مدل:
    - اگر پیام ریپلای نداشته باشد (مثلاً فقط تگ شده): سشن صفر برمی‌گرداند ([]).
    - اگر ریپلای داشته باشد: کل بحث‌های قبل از این پیام را به ترتیب زمانی استخراج می‌کند:
      * پیام‌های خود ربات: role='assistant'
      * پیام‌های کاربران گروه (هر چند نفر که باشند): role='user' با فرمت [نام کاربر]: متن پیام
    """
    # ثبت پیام فعلی در کش
    record_message(message)

    # اگر کاربر روی پیامی ریپلای نزده باشد (فقط تگ شده) -> سشن صفر
    if not message.reply_to_message:
        return []

    # ثبت پیام والد مستقیم در کش
    record_message(message.reply_to_message)

    chain: list[dict[str, str]] = []
    visited: set[int] = {message.message_id}
    curr_id = message.reply_to_message.message_id
    depth = 0

    while curr_id and depth < max_depth and curr_id not in visited:
        visited.add(curr_id)
        info = get_message_info(curr_id, message.chat.id)
        if not info:
            break

        text = (info.get("text") or "").strip()
        if text:
            is_bot = info.get("is_bot") or (info.get("sender_id") == bot_id)
            if is_bot:
                chain.insert(0, {
                    "role": "assistant",
                    "content": text[:1500],
                })
            else:
                sender_name = info.get("sender_name", "کاربر")
                chain.insert(0, {
                    "role": "user",
                    "content": f"[{sender_name}]: {text[:1500]}",
                })

        curr_id = info.get("reply_to_msg_id")
        depth += 1

    return chain


from aiogram import BaseMiddleware

class ThreadTrackerMiddleware(BaseMiddleware):
    """
    میدل‌ور اختصاصی ردیابی پیام‌های گروه و ایندکس زنده در پس‌زمینه:
    ۱. ثبت فوری پیام در کش حافظه برای بازسازی زنجیره ریپلای‌ها
    ۲. ارسال پیام به تسک پس‌زمینه (Asyncio Task) جهت ذخیره در SQLite و تولید وکتور Qdrant بدون توقف پردازش ربات
    """
    async def __call__(self, handler, event: Any, data: dict[str, Any]) -> Any:
        if isinstance(event, Message):
            record_message(event)
            # لانچ تسک ایندکس زنده به صورت کاملاً غیرمسدودکننده در پس‌زمینه
            try:
                import asyncio
                from bot.live_indexer import ingest_incoming_message
                bot_obj = data.get("bot")
                bot_id = bot_obj.id if bot_obj else None
                asyncio.create_task(ingest_incoming_message(event, bot_id))
            except Exception as e:
                logger.debug(f"Could not spawn live ingest task: {e}")

        return await handler(event, data)


