from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
from typing import Any, Awaitable, Callable
import openai

from bot.config import bot_settings
from bot.formatters import escape_html, format_rag_response, format_rag_rich_html
from bot.providers import ProviderRegistry
from bot.tools import execute_tool, get_active_tools_definitions
from core.db import get_db

logger = logging.getLogger("bot.rag_engine")


class RAGResult(str):
    """رشته حاوی پاسخ HTML با ویژگی‌های تکمیلی برای تلگرام Rich Message."""
    html: str
    rich_html: str

    def __new__(cls, html_str: str, rich_html_str: str):
        obj = super().__new__(cls, html_str)
        obj.html = html_str
        obj.rich_html = rich_html_str
        return obj


class RAGEngine:
    """
    موتور هوشمند استدلال فنی و تعاملی (Agentic RAG Engine)
    پشتیبانی از:
    - دو اسلات مجزای استدلالی (Reasoning) و پرسرعت (Fast)
    - فراخوانی ابزارها بر اساس تقاضای خود مدل (Agentic Tool Calling)
    - حذف جستجوی ایستا؛ جستجو در آرشیو گروه یا وب فقط زمانی انجام می‌شود که مدل تصمیم بگیرد.
    """

    async def answer_question(
        self,
        question: str,
        sender_name: str = "کاربر",
        thread_messages: list[dict[str, str]] | None = None,
        slot: str = "reasoning",
        progress_callback: Callable[[str], Awaitable[None]] | None = None,
        stream_callback: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> str:
        start_time = time.time()

        # ۱. استخراج کلاینت و نام مدل متناسب با اسلات انتخابی
        client, active_model = ProviderRegistry.get_client_for_slot(slot)
        logger.info(f"Using slot='{slot}' with model='{active_model}'")

        # ۲. ابزارهای فعال بر اساس تنظیمات ادمین
        active_tools = get_active_tools_definitions() if bot_settings.enable_tools else []
        tools_param = active_tools if active_tools else None

        # ۳. ساخت پیام‌های مکالمه
        system_instruction = bot_settings.system_prompt
        if active_tools:
            system_instruction += """

---
🛠 <b>دسترسی به ابزارهای هوشمند:</b>
شما دسترسی مستقیم به ابزارهای زیر دارید:
1. `search_group_chat`: جستجو در آرشیو تجربیات و مکالمات اعضای گروه موتورسیکلت (مشکلات فنی، مکانیک‌ها، روغن‌ها، پارت‌نامبرها).
2. `get_surrounding_messages`: مشاهده پیام‌های قبل و بعد یک پیام در چت گروه برای فهم زمینه گفتگو.
3. `search_web`: جستجوی آزاد در وب و یوتیوب برای ویدیوهای آموزشی، دفترچه‌های تعمیراتی کارخانه (OEM Manual) و مشخصات استاندارد. در صورت نیاز به جستجوی ویدیو یا منوال، عبارات کلیدی انگلیسی یا فارسی دقیق بسازید.

دستورالعمل استفاده از ابزارها:
- اگر سوال نیاز به بررسی تجارب اعضا یا شواهد خیابان‌های ایران دارد، حتماً `search_group_chat` را فراخوانی کنید.
- اگر سوال نیاز به ویدیو یا مشخصات رسمی کاتالوگ کارخانه دارد، `search_web` را فراخوانی کنید.
- اگر موضوع را از نظر علمی و مهندسی با اطمینان ۱۰۰٪ می‌دانید، می‌توانید مستقیماً بدون ابزار پاسخ دهید.
"""

        chat_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_instruction}
        ]

        if thread_messages:
            for tm in thread_messages:
                chat_messages.append({"role": "user", "content": f"[{tm.get('sender', 'کاربر')}]: {tm.get('text', '')}"})

        chat_messages.append({"role": "user", "content": f"سوال کاربر ({sender_name}): {question}"})

        # ۴. حلقه پردازش چندمرحله‌ای عامل (Agentic Loop)
        max_tool_turns = 4
        current_turn = 0
        final_answer_chunks: list[str] = []
        full_reasoning_chunks: list[str] = []
        collected_message_links: dict[str, str] = {}
        total_inspected = 0
        web_sources_count = 0
        last_progress_edit: float = 0.0

        while current_turn < max_tool_turns:
            current_turn += 1
            turn_content_chunks: list[str] = []
            turn_reasoning_chunks: list[str] = []
            tool_calls_acc: dict[int, dict[str, Any]] = {}

            # ارسال درخواست به مدل با ابزارهای فعال (در صورت وجود)
            call_kwargs: dict[str, Any] = {
                "model": active_model,
                "messages": chat_messages,
                "temperature": 0.2,
                "stream": True,
            }
            if tools_param and current_turn < max_tool_turns:
                call_kwargs["tools"] = tools_param
            if slot == "fast":
                call_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

            try:
                stream = await client.chat.completions.create(**call_kwargs)
            except Exception as e:
                if "thinking" in str(e) or "extra_body" in str(e):
                    call_kwargs.pop("extra_body", None)
                    stream = await client.chat.completions.create(**call_kwargs)
                elif "thought_signature" in str(e) and "tools" in call_kwargs:
                    logger.warning(f"thought_signature issue detected on {active_model}, retrying without tools: {e}")
                    call_kwargs.pop("tools", None)
                    stream = await client.chat.completions.create(**call_kwargs)
                else:
                    raise e

            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                # دریافت توکن‌های استدلال (مخصوص مدل‌های فکری مثل GLM-5 یا DeepSeek-R1)
                r_part = getattr(delta, "reasoning_content", None)
                if r_part:
                    turn_reasoning_chunks.append(r_part)
                    full_reasoning_chunks.append(r_part)
                    now = time.time()
                    if stream_callback and (now - last_progress_edit >= 1.2):
                        last_progress_edit = now
                        reasoning_so_far = "".join(full_reasoning_chunks)
                        try:
                            await stream_callback(reasoning_so_far, "")
                        except Exception:
                            pass
                    elif progress_callback and (now - last_progress_edit >= 3.0):
                        last_progress_edit = now
                        recent_text = "".join(turn_reasoning_chunks[-20:]).strip()
                        lines = [l.strip() for l in recent_text.split("\n") if l.strip()]
                        latest_line = lines[-1] if lines else recent_text
                        if len(latest_line) > 90:
                            latest_line = latest_line[-90:]
                        status = (
                            f"🧠 <b>در حال تحلیل و استدلال فنی...</b>\n"
                            f"💭 <i>{html.escape(latest_line)}...</i>"
                        )
                        try:
                            await progress_callback(status)
                        except Exception:
                            pass

                # دریافت محتوای متنی و استریم زنده به تلگرام
                if delta.content:
                    turn_content_chunks.append(delta.content)
                    now = time.time()
                    if stream_callback and (now - last_progress_edit >= 1.2):
                        last_progress_edit = now
                        reasoning_so_far = "".join(full_reasoning_chunks)
                        content_so_far = "".join(turn_content_chunks)
                        try:
                            await stream_callback(reasoning_so_far, content_so_far)
                        except Exception:
                            pass
                    elif not final_answer_chunks and progress_callback and slot != "fast":
                        try:
                            await progress_callback("✍️ <b>در حال نگارش پاسخ مستند...</b>")
                        except Exception:
                            pass

                # دریافت ابزارهای فراخوانی شده
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index if tc.index is not None else 0
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                "id": tc.id or "",
                                "name": "",
                                "arguments": "",
                                "extra_content": None,
                            }
                        if tc.id:
                            tool_calls_acc[idx]["id"] = tc.id
                        if tc.function and tc.function.name:
                            # در صورتی که نام ابزار قبلاً کامل نشده باشد اضافه می‌شود
                            if not tool_calls_acc[idx]["name"]:
                                tool_calls_acc[idx]["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            tool_calls_acc[idx]["arguments"] += tc.function.arguments

                        # استخراج و نگهداری extra_content برای سازگاری با Google Gemini (thought_signature)
                        extra = getattr(tc, "extra_content", None)
                        if extra:
                            if hasattr(extra, "model_dump"):
                                extra = extra.model_dump()
                            tool_calls_acc[idx]["extra_content"] = extra

            # اگر مدل ابزاری را فراخوانی کرده باشد:
            if tool_calls_acc:
                assistant_tool_calls = []
                for tc in tool_calls_acc.values():
                    tc_item = {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": tc["arguments"],
                        },
                    }
                    if tc.get("extra_content"):
                        tc_item["extra_content"] = tc["extra_content"]
                    assistant_tool_calls.append(tc_item)

                chat_messages.append({
                    "role": "assistant",
                    "content": "".join(turn_content_chunks) if turn_content_chunks else None,
                    "tool_calls": assistant_tool_calls,
                })

                # اجرای هر یک از ابزارها
                for tc in tool_calls_acc.values():
                    t_name = tc["name"]
                    raw_args = tc["arguments"]
                    try:
                        args_dict = json.loads(raw_args) if raw_args else {}
                    except Exception:
                        args_dict = {}

                    # اطلاع‌رسانی وضعیت به کاربر در تلگرام
                    if progress_callback:
                        if t_name == "search_group_chat":
                            q_text = args_dict.get("query", "")
                            await progress_callback(f"🔍 <b>جستجو در سوابق گروه:</b> <code>{html.escape(q_text[:35])}</code>")
                        elif t_name == "search_web":
                            q_text = args_dict.get("query", "")
                            await progress_callback(f"🌐 <b>استعلام وب و ویدیوها:</b> <code>{html.escape(q_text[:35])}</code>")
                        elif t_name == "get_surrounding_messages":
                            mid = args_dict.get("message_id", "")
                            await progress_callback(f"📜 <b>بررسی پیام‌های مرتبط:</b> <code>#{mid}</code>")

                    logger.info(f"Executing tool {t_name} with args {args_dict}")
                    tool_res_str = await execute_tool(t_name, args_dict)

                    # استخراج لینک‌ها و آمارهای بازگشتی جهت نمایش در پانویس
                    if t_name == "search_group_chat":
                        try:
                            parsed_res = json.loads(tool_res_str)
                            if isinstance(parsed_res, list):
                                total_inspected += len(parsed_res)
                                for item in parsed_res:
                                    for mid in item.get("message_ids", []):
                                        # ایجاد لینک تلگرام اگر آیدی چت در دسترس باشد
                                        m_str = str(mid)
                                        if m_str not in collected_message_links:
                                            collected_message_links[m_str] = ""
                        except Exception:
                            pass
                    elif t_name == "get_surrounding_messages":
                        try:
                            parsed_res = json.loads(tool_res_str)
                            if isinstance(parsed_res, list):
                                for item in parsed_res:
                                    mid = item.get("message_id")
                                    if mid:
                                        m_str = str(mid)
                                        if m_str not in collected_message_links:
                                            collected_message_links[m_str] = ""
                        except Exception:
                            pass
                    elif t_name == "search_web":
                        try:
                            parsed_res = json.loads(tool_res_str)
                            if isinstance(parsed_res, list):
                                web_sources_count += len(parsed_res)
                        except Exception:
                            pass

                    chat_messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": tool_res_str,
                    })

                # حلقه ادامه می‌یابد تا مدل نتیجه ابزار را تحلیل و پاسخ نهایی دهد
                continue

            # اگر ابزاری فراخوانی نشد، به پاسخ نهایی رسیده‌ایم
            final_answer_chunks = turn_content_chunks
            break

        # ۵. آماده‌سازی متن پاسخ نهایی
        raw_answer = "".join(final_answer_chunks).strip()
        if not raw_answer:
            raw_answer = "⚠️ پاسخی از مدل هوش مصنوعی دریافت نشد. لطفاً مجدداً سوال را ارسال کنید یا مدل را بررسی نمایید."
        full_reasoning = "".join(full_reasoning_chunks).strip()
        elapsed = time.time() - start_time

        # آماده‌سازی بخش استدلال تاشو در صورت وجود
        reasoning_html = ""
        if full_reasoning and len(full_reasoning) > 30 and slot != "fast":
            lines = [l.strip() for l in full_reasoning.split("\n") if l.strip()]
            summary_lines = lines[:4]
            compact_text = "\n".join(summary_lines)[:350]
            reasoning_html = (
                f"\n\n<blockquote expandable><b>🧠 روند استدلال و راستی‌آزمایی مدل:</b>\n"
                f"<i>{escape_html(compact_text)}...</i></blockquote>"
            )

        # استخراج تمامی شناسه‌های پیام ذکرشده در متن پاسخ مدل برای اطمینان از ساخت لینک
        for m_tag in re.finditer(r"\[(?:(?:msg|message)s?[:\s]|#)\s*([^\]]+)\]", raw_answer, re.IGNORECASE):
            for m_num in re.findall(r"\b\d+\b", m_tag.group(1)):
                if m_num not in collected_message_links:
                    collected_message_links[m_num] = ""

        # تکمیل لینک‌های تلگرام برای پیام‌های یافته‌شده با استفاده از دیتابیس پیام‌ها
        if collected_message_links:
            try:
                with get_db() as conn:
                    mids = [int(m) for m in collected_message_links.keys() if m.isdigit()]
                    if mids:
                        placeholders = ",".join("?" for _ in mids)
                        rows = conn.execute(
                            f"SELECT message_id, chat_id FROM messages WHERE message_id IN ({placeholders})",
                            mids,
                        ).fetchall()
                        for r in rows:
                            mid = str(r["message_id"])
                            cid = r["chat_id"]
                            if cid:
                                clean_cid = str(cid).replace("-100", "")
                                collected_message_links[mid] = f"https://t.me/c/{clean_cid}/{mid}"
            except Exception as e:
                logger.warning(f"Error enriching message links from sqlite: {e}")

        # ۶. قالب‌بندی نهایی به سبک استاندارد تلگرام و ساختار جدید Rich Message
        formatted_html = format_rag_response(
            answer=raw_answer,
            message_links=collected_message_links,
            model_name=active_model,
            elapsed_time=elapsed,
            total_inspected=total_inspected,
            web_sources_count=web_sources_count,
        )

        formatted_rich_html = format_rag_rich_html(
            answer=raw_answer,
            message_links=collected_message_links,
            model_name=active_model,
            elapsed_time=elapsed,
            total_inspected=total_inspected,
            web_sources_count=web_sources_count,
        )

        if reasoning_html:
            formatted_html = formatted_html + reasoning_html
            formatted_rich_html = formatted_rich_html + "\n\n" + reasoning_html

        return RAGResult(formatted_html, formatted_rich_html)


rag_engine = RAGEngine()
