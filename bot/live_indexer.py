from __future__ import annotations

import datetime
import logging
from typing import Any
from aiogram.types import Message

from bot.config import bot_settings
from core.db import get_db
from core.repo import upsert_messages_batch
from vector_store.qdrant_store import QdrantVectorStore, get_qdrant_store

logger = logging.getLogger("bot.live_indexer")


def get_vector_store() -> QdrantVectorStore:
    return get_qdrant_store(
        url=bot_settings.qdrant_url or None,
        api_key=bot_settings.qdrant_api_key or None,
        db_path=bot_settings.qdrant_path,
        hf_token=bot_settings.hf_token,
        collection_name=bot_settings.qdrant_collection,
    )



async def ingest_incoming_message(message: Message, bot_id: int | None = None) -> None:
    """
    ذخیره‌سازی و ایندکس بلادرنگ و ناهمگام (Async) پیام‌های گروه:
    ۱. ثبت سریع در دیتابیس SQLite
    ۲. در صورت فعال بودن Live Ingestion و داشتن متن فنی/مفید، امبدینگ در پس‌زمینه و ثبت در Qdrant
    """
    if not message or not message.message_id:
        return

    # چشم‌پوشی از پیام‌های ارسالی توسط خود ربات یا دستورات بات
    if message.from_user and (message.from_user.is_bot or (bot_id and message.from_user.id == bot_id)):
        return

    text = (message.text or message.caption or "").strip()
    if text.startswith("/"):
        return

    sender_id = message.from_user.id if message.from_user else None
    sender_name = (
        (message.from_user.full_name or message.from_user.username)
        if message.from_user
        else "کاربر"
    )
    sender_username = message.from_user.username if message.from_user else None
    sender_type = "user"

    reply_to_msg_id = message.reply_to_message.message_id if message.reply_to_message else None
    date_str = message.date.isoformat() if message.date else datetime.datetime.now(datetime.timezone.utc).isoformat()

    media_type = None
    if message.photo:
        media_type = "photo"
    elif message.video:
        media_type = "video"
    elif message.voice:
        media_type = "voice"
    elif message.audio:
        media_type = "audio"
    elif message.document:
        media_type = "document"

    record: dict[str, Any] = {
        "message_id": message.message_id,
        "chat_id": message.chat.id,
        "sender_id": sender_id,
        "sender_type": sender_type,
        "sender_name": sender_name,
        "sender_username": sender_username,
        "text": text,
        "raw_text": text,
        "reply_to_msg_id": reply_to_msg_id,
        "reply_to_top_id": None,
        "is_topic_message": 0,
        "date": date_str,
        "edit_date": None,
        "is_forward": 1 if message.forward_date else 0,
        "forward_from_id": message.forward_from.id if message.forward_from else None,
        "forward_from_name": message.forward_from.full_name if message.forward_from else None,
        "forward_date": message.forward_date.isoformat() if message.forward_date else None,
        "media_type": media_type,
        "file_id": None,
        "file_unique_id": None,
        "file_name": None,
        "mime_type": None,
        "file_size": None,
        "duration": None,
        "width": None,
        "height": None,
        "grouped_id": None,
        "views": None,
        "forwards": None,
        "replies_count": 0,
        "reactions_json": None,
        "raw_json": None,
    }

    # ۱. ذخیره فوری در SQLite
    try:
        with get_db() as conn:
            upsert_messages_batch(conn, [record])
    except Exception as e:
        logger.error(f"Error saving live message #{message.message_id} to SQLite: {e}")

    # ۲. امبدینگ و ثبت در Qdrant (در صورت فعال بودن و داشتن حداقل ۱۲ کاراکتر متن)
    if not bot_settings.enable_live_ingest:
        return

    if len(text) < 12:
        return

    try:
        vs = get_vector_store()
        # تولید اسینک وکتور
        vector = await vs.get_embedding_async(text)

        # ساخت لینک تلگرام
        c_id = message.chat.id
        link_cid = str(c_id).replace("-100", "") if str(c_id).startswith("-100") else str(abs(c_id))
        msg_link = f"https://t.me/c/{link_cid}/{message.message_id}"

        # ساخت شناسه عددی مثبت یکتا برای کیودرنت (از ترکیب chat_id و message_id)
        point_id = abs(hash(f"{message.chat.id}_{message.message_id}")) % (2**63 - 1)

        payload = {
            "thread_id": f"live_{message.message_id}",
            "chat_id": message.chat.id,
            "root_message_id": reply_to_msg_id or message.message_id,
            "message_ids": [message.message_id],
            "senders": [sender_name],
            "date": date_str,
            "text": f"[{sender_name}]: {text}",
            "message_links": {f"msg:{message.message_id}": msg_link},
        }

        # آپلود بدون بلاک کردن لوپ
        import asyncio
        await asyncio.to_thread(vs.upsert_point, point_id, vector, payload)
        logger.debug(f"Live indexed message #{message.message_id} into Qdrant successfully.")

    except Exception as q_err:
        logger.warning(f"Error indexing live message #{message.message_id} to Qdrant: {q_err}")
