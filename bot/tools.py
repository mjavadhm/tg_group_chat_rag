from __future__ import annotations

import logging
from typing import Any

from bot.config import bot_settings
from bot.web_search import search_web as _search_web
from core.db import get_db
from vector_store.qdrant_store import QdrantVectorStore, get_qdrant_store

logger = logging.getLogger("bot.tools")


def get_vector_store() -> QdrantVectorStore:
    return get_qdrant_store(
        url=bot_settings.qdrant_url or None,
        api_key=bot_settings.qdrant_api_key or None,
        db_path=bot_settings.qdrant_path,
        hf_token=bot_settings.hf_token,
        collection_name=bot_settings.qdrant_collection,
    )



async def tool_search_group_chat(query: str, limit: int | None = None) -> list[dict[str, Any]]:
    """جستجوی معنایی پیام‌ها و تردهای تجربیات اعضای گروه."""
    effective_limit = limit if (limit and limit > 0) else bot_settings.rag_top_k
    if effective_limit < bot_settings.rag_top_k:
        effective_limit = bot_settings.rag_top_k
    vs = get_vector_store()
    try:
        results = await vs.search_async(query=query, limit=effective_limit, min_score=0.25)
        clean_results = []
        for r in results:
            clean_results.append({
                "score": round(r.get("score", 0), 2),
                "text": r.get("text"),
                "senders": r.get("senders", []),
                "date": r.get("date"),
                "message_ids": r.get("message_ids", []),
            })
        return clean_results
    except Exception as e:
        logger.error(f"tool_search_group_chat error: {e}")
        return [{"error": str(e)}]


async def tool_get_surrounding_messages(
    message_id: int,
    chat_id: int | None = None,
    before: int = 3,
    after: int = 3,
) -> list[dict[str, Any]]:
    """
    استخراج پنجره زمانی پیام‌های قبل و بعد از یک پیام در دیتابیس SQLite:
    این ابزار امکان دیدن کل گفتگوی متوالی که در آن لحظه شکل گرفته را فراهم می‌کند.
    """
    try:
        with get_db() as conn:
            # واکشی پیام هدف
            target = conn.execute(
                "SELECT message_id, chat_id, date FROM messages WHERE message_id = ?",
                (message_id,)
            ).fetchone()

            if not target:
                return [{"note": f"پیام با شناسه {message_id} در دیتابیس یافت نشد."}]

            c_id = target["chat_id"] if chat_id is None else chat_id
            target_date = target["date"]

            # پیام‌های قبل
            before_rows = conn.execute(
                """
                SELECT message_id, sender_name, text, date, reply_to_msg_id
                FROM messages
                WHERE chat_id = ? AND date < ? AND text IS NOT NULL AND text != ''
                ORDER BY date DESC
                LIMIT ?
                """,
                (c_id, target_date, before),
            ).fetchall()

            # پیام‌های بعد
            after_rows = conn.execute(
                """
                SELECT message_id, sender_name, text, date, reply_to_msg_id
                FROM messages
                WHERE chat_id = ? AND date > ? AND text IS NOT NULL AND text != ''
                ORDER BY date ASC
                LIMIT ?
                """,
                (c_id, target_date, after),
            ).fetchall()

            # مرتب‌سازی به ترتیب زمانی
            chronological = list(reversed(before_rows)) + [target] + list(after_rows)

            output = []
            for r in chronological:
                output.append({
                    "message_id": r["message_id"],
                    "sender": r["sender_name"] if "sender_name" in r.keys() else "کاربر",
                    "text": r["text"] if "text" in r.keys() else "",
                    "reply_to": r["reply_to_msg_id"] if "reply_to_msg_id" in r.keys() else None,
                })
            return output

    except Exception as e:
        logger.error(f"tool_get_surrounding_messages error: {e}")
        return [{"error": str(e)}]


async def tool_search_web(query: str) -> list[dict[str, Any]]:
    """جستجوی سریع در اینترنت برای دفترچه‌های فنی، کاتالوگ کارخانه، مشخصات پارت‌نامبرها."""
    try:
        return await _search_web(query=query, max_results=3)
    except Exception as e:
        logger.error(f"tool_search_web error: {e}")
        return [{"error": str(e)}]


# تعریف اسکیمای استاندارد ابزارهای OpenAI-compatible
TOOLS_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_group_chat",
            "description": "جستجوی سوابق و تجربیات واقعی اعضای گروه موتورسیکلت در مورد خرابی‌ها، تجارب روغن، قطعات و مکانیک‌ها.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "عبارت جستجو برای تردهای گروه (مثلاً: روغن مناسب گرما، رفع صدای ناک)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "حداکثر تعداد تردهای بازیابی‌شده (پیش‌فرض ۵)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_surrounding_messages",
            "description": "دریافت پیام‌های قبل و بعد یک پیام در چت گروه برای فهمیدن پیش‌زمینه و ادامه بحث.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "integer",
                        "description": "شناسه پیام مورد نظر",
                    },
                    "before": {
                        "type": "integer",
                        "description": "تعداد پیام‌های قبل (پیش‌فرض ۳)",
                    },
                    "after": {
                        "type": "integer",
                        "description": "تعداد پیام‌های بعد (پیش‌فرض ۳)",
                    },
                },
                "required": ["message_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "جستجوی زنده در اینترنت و یوتیوب برای استعلام مشخصات رسمی کارخانه، فیلر شمع، ویدیوهای آموزشی، کد خطا و منوال تعمیراتی موتورسیکلت.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "عبارت فنی جهت جستجو در وب یا یوتیوب (مثلاً: Bajaj Dominar 250 spark plug replacement youtube)",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


def get_active_tools_definitions() -> list[dict[str, Any]]:
    """فیلتر کردن ابزارهای فعال بر اساس تنظیمات ادمین در تلگرام."""
    if not bot_settings.enable_tools:
        return []

    import copy
    active = []
    for t in TOOLS_DEFINITIONS:
        name = t["function"]["name"]
        if name == "search_group_chat" and bot_settings.enable_tool_group_search:
            t_copy = copy.deepcopy(t)
            t_copy["function"]["parameters"]["properties"]["limit"]["description"] = (
                f"حداکثر تعداد تردهای بازیابی‌شده (پیش‌فرض سیستم: {bot_settings.rag_top_k})"
            )
            active.append(t_copy)
        elif name == "get_surrounding_messages" and bot_settings.enable_tool_surrounding:
            active.append(t)
        elif name == "search_web" and bot_settings.enable_tool_web_search:
            active.append(t)
    return active


async def execute_tool(tool_name: str, arguments: dict[str, Any]) -> str:
    """اجرای ابزار فراخوانی شده توسط مدل و برگرداندن پاسخ به صورت رشته JSON."""
    import json
    try:
        if tool_name == "search_group_chat":
            query = arguments.get("query", "")
            req_limit = arguments.get("limit")
            limit = int(req_limit) if req_limit else bot_settings.rag_top_k
            if limit < bot_settings.rag_top_k:
                limit = bot_settings.rag_top_k
            res = await tool_search_group_chat(query, limit)
            return json.dumps(res, ensure_ascii=False)
        elif tool_name == "get_surrounding_messages":
            mid = int(arguments.get("message_id", 0))
            before = int(arguments.get("before", 3))
            after = int(arguments.get("after", 3))
            res = await tool_get_surrounding_messages(mid, before=before, after=after)
            return json.dumps(res, ensure_ascii=False)
        elif tool_name == "search_web":
            query = arguments.get("query", "")
            res = await tool_search_web(query)
            return json.dumps(res, ensure_ascii=False)
        else:
            return json.dumps({"error": f"ابزار ناشناخته: {tool_name}"}, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error executing tool {tool_name}: {e}", exc_info=True)
        return json.dumps({"error": str(e)}, ensure_ascii=False)

