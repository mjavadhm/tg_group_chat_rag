"""ماژول پشتیبانی از متدهای جدید Telegram Bot API (نسخه 10.1+)
شامل Rich Messages، استریمینگ زنده Draft، جداول نیتیو، بلوک‌های تاشو و راست‌به‌چپ (RTL).

معماری استریم:
• چت خصوصی (Private): از sendRichMessageDraft با بلوک‌های نیتیو Thinking استفاده می‌شود.
• سوپرگروه (Supergroup): Draft API خطای TEXTDRAFT_PEER_INVALID می‌دهد؛
  بنابراین از edit_text با بهترین قالب‌بندی HTML ممکن استفاده می‌شود.
• پیام نهایی: در هر دو حالت از sendRichMessage با جداول، بخش تاشو و RTL استفاده می‌شود.
"""

from __future__ import annotations

import logging
from typing import Any
from aiogram import Bot
from aiogram.methods.base import TelegramMethod

logger = logging.getLogger(__name__)


# ─── متدهای سفارشی Bot API ───────────────────────────────────────────────────

class SendMessageDraft(TelegramMethod[bool]):
    """ارسال استریم پیش‌نویس متن ساده (Text Stream/Draft)."""
    __returning__ = bool
    __api_method__ = "sendMessageDraft"

    chat_id: int
    draft_id: int
    text: str | None = None
    message_thread_id: int | None = None
    parse_mode: str | None = None
    can_stop: bool | None = None
    keep_on_stop: bool | None = None


class SendRichMessageDraft(TelegramMethod[bool]):
    """ارسال استریم پیش‌نویس غنی (Rich Message Draft) با پشتیبانی از Thinking و Blocks."""
    __returning__ = bool
    __api_method__ = "sendRichMessageDraft"

    chat_id: int
    draft_id: int
    rich_message: dict[str, Any]
    message_thread_id: int | None = None
    can_stop: bool | None = None
    keep_on_stop: bool | None = None


class SendRichMessage(TelegramMethod[dict[str, Any]]):
    """ارسال پیام کامل Rich Message با جداول، بلوک‌های تاشو و ساختار چندبلوکی."""
    __returning__ = dict
    __api_method__ = "sendRichMessage"

    chat_id: int | str
    rich_message: dict[str, Any]
    message_thread_id: int | None = None
    reply_parameters: dict[str, Any] | None = None
    reply_markup: Any | None = None
    disable_notification: bool | None = None
    protect_content: bool | None = None


# ─── استریم Rich Draft (فقط چت خصوصی) ────────────────────────────────────────

async def send_rich_draft_stream(
    bot: Bot,
    chat_id: int,
    draft_id: int,
    reasoning: str = "",
    content: str = "",
    can_stop: bool = True,
) -> bool:
    """
    ارسال استریم پیش‌نویس نیتیو Rich Message به چت خصوصی.
    
    بلوک‌های استفاده‌شده:
    • فقط استدلال → بلوک thinking نیتیو
    • استدلال + محتوا → بلوک thinking + بلوک‌های paragraph
    • فقط محتوا → بلوک paragraph با نشانگر تایپ ▌
    """
    try:
        blocks: list[dict[str, Any]] = []

        if reasoning:
            # بلوک Thinking نیتیو - آخرین ۶۰۰ کاراکتر برای جلوگیری از سرریز
            thinking_text = reasoning[-600:] if len(reasoning) > 600 else reasoning
            blocks.append({
                "type": "thinking",
                "text": thinking_text,
            })

        if content:
            # بلوک محتوای در حال نگارش با نشانگر زنده
            blocks.append({
                "type": "paragraph",
                "text": f"{content} ▌",
            })

        if not blocks:
            # حالت اولیه: فقط بلوک Thinking خالی برای نمایش انیمیشن
            blocks.append({
                "type": "thinking",
                "text": "...",
            })

        rich_msg: dict[str, Any] = {
            "blocks": blocks,
            "is_rtl": True,
        }

        await bot(SendRichMessageDraft(
            chat_id=chat_id,
            draft_id=draft_id,
            rich_message=rich_msg,
            can_stop=can_stop,
        ))
        return True

    except Exception as e:
        logger.debug("Failed to send rich draft stream: %s", e)
        return False


# ─── ارسال پیام نهایی Rich Message (همه انواع چت) ────────────────────────────

async def send_final_rich_message(
    bot: Bot,
    chat_id: int | str,
    rich_html: str,
    reply_to_message_id: int | None = None,
    reply_markup: Any | None = None,
) -> dict[str, Any] | None:
    """ارسال پیام نهایی با ساختار نیتیو Rich Message تلگرام و تراز راست‌به‌چپ (RTL)."""
    try:
        reply_params = None
        if reply_to_message_id:
            reply_params = {"message_id": reply_to_message_id}

        payload: dict[str, Any] = {
            "html": rich_html,
            "is_rtl": True,
        }

        res = await bot(SendRichMessage(
            chat_id=chat_id,
            rich_message=payload,
            reply_parameters=reply_params,
            reply_markup=reply_markup,
        ))
        return res
    except Exception as e:
        logger.warning("Error sending rich message via sendRichMessage: %s", e)
        return None
