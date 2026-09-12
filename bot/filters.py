from __future__ import annotations

import re
from typing import Any
from aiogram import Bot
from aiogram.filters import BaseFilter
from aiogram.types import Message

from bot.config import bot_settings
from bot.thread_manager import extract_thread_context, record_message


class IsAdmin(BaseFilter):
    """فیلتر بررسی ادمین بودن کاربر تلگرام."""

    async def __call__(self, message: Message) -> bool:
        if not message.from_user:
            return False
        return bot_settings.is_admin(message.from_user.id)


class ShouldRespond(BaseFilter):
    """
    فیلتر هوشمند بررسی لزوم پاسخ‌دهی ربات و استخراج زمینه ترد:
    ۱. در چت خصوصی (پی‌وی): به پیام‌ها پاسخ می‌دهد.
    ۲. در گروه/سوپرگروه:
       - اگر فقط تگ شود (بدون ریپلای): سشن صفر و کاملاً تمیز است.
       - اگر روی پیامی ریپلای زده باشد: کل ترد و بحث‌های قبل را استخراج می‌کند
         (پیام‌های ربات assistant و پیام‌های کاربران user).
    """

    async def __call__(self, message: Message, bot: Bot) -> bool | dict[str, Any]:
        if not message.text:
            return False

        # ثبت پیام در کش تردهای چت
        record_message(message)

        bot_info = await bot.get_me()
        bot_username = (bot_info.username or "").lower()

        is_private = message.chat.type == "private"
        is_reply_to_bot = (
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.id == bot_info.id
        )
        is_mentioned = bool(bot_username and f"@{bot_username}" in message.text.lower())

        if not (is_private or is_reply_to_bot or is_mentioned):
            return False

        clean_query = message.text.strip()
        if is_mentioned and bot_username:
            clean_query = re.sub(rf"@{bot_username}\b", "", clean_query, flags=re.IGNORECASE).strip()

        if not clean_query and message.reply_to_message:
            clean_query = "لطفاً این پیام و موضوع را بررسی کن و نظرت و تجربیات گروه را بگو."

        if not clean_query:
            return False

        # استخراج زنجیره گفتگو بر اساس ترد ریپلای (تگ بدون ریپلای = سشن صفر)
        thread_messages = extract_thread_context(message, bot_info.id)

        user_name = "کاربر"
        if message.from_user:
            user_name = message.from_user.full_name or message.from_user.username or "کاربر"

        return {
            "query": clean_query,
            "sender_name": user_name,
            "thread_messages": thread_messages,
        }
