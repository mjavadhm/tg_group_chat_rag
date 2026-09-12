from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rich.console import Console
from rich.table import Table

from core.config import settings
from core.db import get_db, init_db
from processor.classifier import ensure_classification_columns

console = Console()

MAX_MESSAGES_PER_THREAD_CHUNK = 8  # اگر ترگی خیلی طولانی شد، هر ۸ پیام یک چانک شود تا کانتکست بیش از حد شلوغ نشود


@dataclass
class ChatMessage:
    id: int
    message_id: int
    chat_id: int
    sender_id: int | None
    sender_name: str
    text: str
    reply_to_msg_id: int | None
    date: str
    media_type: str | None
    content_category: str
    is_embeddable: int
    reactions_summary: str = ""


@dataclass
class ThreadChunk:
    thread_id: str
    chat_id: int
    root_message_id: int
    message_ids: list[int] = field(default_factory=list)
    senders: list[str] = field(default_factory=list)
    date: str = ""
    chunk_text: str = ""
    message_links: dict[str, str] = field(default_factory=dict)


def build_telegram_link(chat_id: int, message_id: int, chat_username: str | None = None) -> str:
    """ساخت لینک مستقیم تلگرام به پیام برای رفرنس‌دهی کلیک‌خور."""
    if chat_username:
        return f"https://t.me/{chat_username}/{message_id}"
    clean_id = str(chat_id).replace("-100", "").lstrip("-")
    return f"https://t.me/c/{clean_id}/{message_id}"


def build_threads_from_db(
    min_text_len: int = 5,
    chat_username: str | None = None,
) -> list[ThreadChunk]:
    """
    استخراج پیام‌ها، اتصال درخت ریپلای‌ها و تولید چانک‌های ساختاریافته به همراه تگ [msg:ID]
    برای رفرنس‌دهی دقیق مدل زبانی (LLM Citation).
    """
    init_db()

    with get_db() as conn:
        ensure_classification_columns(conn)
        cursor = conn.cursor()

        # خواندن مشخصات گروه از crawl_state در صورت وجود
        chat_state = cursor.execute("SELECT chat_username FROM crawl_state LIMIT 1").fetchone()
        if chat_state and chat_state["chat_username"]:
            chat_username = chat_state["chat_username"]

        console.print("[cyan]در حال واکشی پیام‌های باارزش از دیتابیس...[/cyan]")
        cursor.execute(
            """
            SELECT id, message_id, chat_id, sender_id, sender_name, text,
                   reply_to_msg_id, date, media_type, content_category, is_embeddable, reactions_json
            FROM messages
            WHERE is_embeddable = 1 AND text IS NOT NULL AND trim(text) != ''
            ORDER BY message_id ASC
            """
        )
        rows = cursor.fetchall()

    messages_by_id: dict[int, ChatMessage] = {}
    reply_children: dict[int, list[int]] = defaultdict(list)
    root_msg_ids: list[int] = []

    for r in rows:
        r_summary = ""
        r_json = r["reactions_json"] if "reactions_json" in r.keys() else None
        if r_json:
            try:
                parsed = json.loads(r_json)
                items = [f"{it.get('emoji')} {it.get('count')}" for it in parsed if it.get("emoji")]
                if items:
                    r_summary = "واکنش‌ها: " + ", ".join(items)
            except Exception:
                pass

        msg = ChatMessage(
            id=r["id"],
            message_id=r["message_id"],
            chat_id=r["chat_id"],
            sender_id=r["sender_id"],
            sender_name=r["sender_name"] or "کاربر",
            text=r["text"].strip(),
            reply_to_msg_id=r["reply_to_msg_id"],
            date=r["date"],
            media_type=r["media_type"],
            content_category=r["content_category"],
            is_embeddable=r["is_embeddable"],
            reactions_summary=r_summary,
        )
        messages_by_id[msg.message_id] = msg


    # تفکیک ریشه‌ها و ارتباطات ریپلای
    for msg_id, msg in messages_by_id.items():
        parent_id = msg.reply_to_msg_id
        if parent_id and parent_id in messages_by_id:
            reply_children[parent_id].append(msg_id)
        else:
            root_msg_ids.append(msg_id)

    console.print(f"[green]✓ {len(messages_by_id):,} پیام معنادار و {len(root_msg_ids):,} ریشه گفتگو شناسایی شد.[/green]")

    chunks: list[ThreadChunk] = []

    for root_id in root_msg_ids:
        root_msg = messages_by_id[root_id]
        children_ids = reply_children.get(root_id, [])

        # ۱. اگر پیام بدون ریپلای است (پیام مستقل یا تک تجربی)
        if not children_ids:
            if len(root_msg.text) < min_text_len:
                continue

            link = build_telegram_link(root_msg.chat_id, root_msg.message_id, chat_username)
            react_str = f" ({root_msg.reactions_summary})" if root_msg.reactions_summary else ""
            chunk_text = (
                f"[گفتگو تاریخ: {root_msg.date[:10]} | آیدی: {root_msg.message_id}]\n"
                f"[msg:{root_msg.message_id}] {root_msg.sender_name}{react_str}: {root_msg.text}"
            )

            chunks.append(
                ThreadChunk(
                    thread_id=f"standalone_{root_msg.message_id}",
                    chat_id=root_msg.chat_id,
                    root_message_id=root_msg.message_id,
                    message_ids=[root_msg.message_id],
                    senders=[root_msg.sender_name],
                    date=root_msg.date,
                    chunk_text=chunk_text,
                    message_links={str(root_msg.message_id): link},
                )
            )
            continue

        # ۲. پیام‌های دارای درخت ریپلای: جمع‌آوری تمام فرزندان تا عمق ۲ یا ۳
        thread_messages: list[ChatMessage] = [root_msg]
        queue = list(children_ids)
        visited = {root_id}

        while queue:
            cid = queue.pop(0)
            if cid in visited or cid not in messages_by_id:
                continue
            visited.add(cid)
            thread_messages.append(messages_by_id[cid])
            # افزودن ریپلای‌های بعدی به صف
            if cid in reply_children:
                queue.extend(reply_children[cid])

        # مرتب‌سازی بر اساس آیدی پیام تا روند گفتگو به ترتیب زمانی باشد
        thread_messages.sort(key=lambda m: m.message_id)

        # تقسیم تردهای خیلی طولانی به زیرچانک‌های حداکثر ۸ تایی
        for sub_idx in range(0, len(thread_messages), MAX_MESSAGES_PER_THREAD_CHUNK):
            sub_batch = thread_messages[sub_idx : sub_idx + MAX_MESSAGES_PER_THREAD_CHUNK]

            # اگر این پارت اول نیست، برای حفظ زمینه گفتگو، سوال اولیه (root) را بالای چانک می‌گذاریم
            lines = [f"[گفتگو تاریخ: {root_msg.date[:10]} | آیدی گفتگو: {root_id}]"]
            if sub_idx > 0:
                lines.append(f"[زمینه سوال اصلی: msg:{root_id}] {root_msg.sender_name}: {root_msg.text[:100]}...")

            sub_msg_ids = []
            sub_senders = set()
            sub_links = {}

            for m in sub_batch:
                sub_msg_ids.append(m.message_id)
                sub_senders.add(m.sender_name)
                sub_links[str(m.message_id)] = build_telegram_link(m.chat_id, m.message_id, chat_username)

                # تگ یکتای پیام [msg:ID] برای رفرنس دادن مدل
                prefix = f"[msg:{m.message_id}]"
                if m.message_id == root_id:
                    role_label = "سوال/موضوع"
                else:
                    role_label = "پاسخ"

                react_str = f" ({m.reactions_summary})" if m.reactions_summary else ""
                lines.append(f"{prefix} {m.sender_name} ({role_label}){react_str}: {m.text}")

            chunk_text = "\n".join(lines)
            chunk_id = f"thread_{root_id}_{sub_idx // MAX_MESSAGES_PER_THREAD_CHUNK + 1}"

            chunks.append(
                ThreadChunk(
                    thread_id=chunk_id,
                    chat_id=root_msg.chat_id,
                    root_message_id=root_id,
                    message_ids=sub_msg_ids,
                    senders=list(sub_senders),
                    date=root_msg.date,
                    chunk_text=chunk_text,
                    message_links=sub_links,
                )
            )

    return chunks


def export_threads_to_jsonl(output_path: Path | str = "data/threads.jsonl") -> list[ThreadChunk]:
    """استخراج تردها و ذخیره در فایل خطی JSONL جهت امبدینگ با کولب یا اسکریپت."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    chunks = build_threads_from_db()

    with open(output_path, "w", encoding="utf-8") as f:
        for ch in chunks:
            record = {
                "thread_id": ch.thread_id,
                "chat_id": ch.chat_id,
                "root_message_id": ch.root_message_id,
                "message_ids": ch.message_ids,
                "senders": ch.senders,
                "date": ch.date,
                "text": ch.chunk_text,
                "message_links": ch.message_links,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    console.print(
        f"\n[bold green]✓ موفقیت:[/bold green] تعداد [bold yellow]{len(chunks):,}[/bold yellow] چانک ترد "
        f"با تگ‌های رفرنس در فایل [cyan]{output_path}[/cyan] ذخیره شد."
    )
    return chunks


if __name__ == "__main__":
    chunks = export_threads_to_jsonl()

    # نمایش ۳ نمونه از چانک‌های خروجی برای اطمینان کاربر
    console.print("\n[bold magenta]🔍 نمونه چانک‌های ساخته‌شده با ساختار ارجاع به منبع:[/bold magenta]\n")
    sample_threads = [c for c in chunks if len(c.message_ids) > 1][:3]

    for idx, sample in enumerate(sample_threads, 1):
        table = Table(title=f"چانک نمونه شماره {idx} ({sample.thread_id})", style="blue")
        table.add_column("محتوای امبد شونده همراه با تگ [msg:ID]", style="white")
        table.add_row(sample.chunk_text)
        console.print(table)
        console.print(f"[dim]لینک‌های استخراج شده برای این چانک: {sample.message_links}[/dim]\n")
