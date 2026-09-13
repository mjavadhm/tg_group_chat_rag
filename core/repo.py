from __future__ import annotations

import sqlite3
from typing import Any, Sequence


def upsert_messages_batch(conn: sqlite3.Connection, msgs: Sequence[dict[str, Any]]) -> int:
    """درج دسته‌ای پیام‌ها با مدیریت جایگزینی در صورت وجود (INSERT OR REPLACE)."""
    if not msgs:
        return 0

    sql = """
    INSERT INTO messages (
        message_id, chat_id, sender_id, sender_type, sender_name, sender_username,
        text, raw_text, reply_to_msg_id, reply_to_top_id, is_topic_message,
        date, edit_date, is_forward, forward_from_id, forward_from_name, forward_date,
        media_type, file_id, file_unique_id, file_name, mime_type, file_size,
        duration, width, height, grouped_id, views, forwards, replies_count,
        reactions_json, raw_json
    ) VALUES (
        :message_id, :chat_id, :sender_id, :sender_type, :sender_name, :sender_username,
        :text, :raw_text, :reply_to_msg_id, :reply_to_top_id, :is_topic_message,
        :date, :edit_date, :is_forward, :forward_from_id, :forward_from_name, :forward_date,
        :media_type, :file_id, :file_unique_id, :file_name, :mime_type, :file_size,
        :duration, :width, :height, :grouped_id, :views, :forwards, :replies_count,
        :reactions_json, :raw_json
    )
    ON CONFLICT(chat_id, message_id) DO UPDATE SET
        edit_date = excluded.edit_date,
        text = excluded.text,
        raw_text = excluded.raw_text,
        views = excluded.views,
        forwards = excluded.forwards,
        replies_count = excluded.replies_count,
        reactions_json = excluded.reactions_json,
        raw_json = excluded.raw_json;
    """

    cursor = conn.cursor()
    cursor.executemany(sql, msgs)
    return cursor.rowcount


def get_crawl_state(conn: sqlite3.Connection, chat_id: int) -> sqlite3.Row | None:
    """دریافت آخرین وضعیت ذخیره‌شده از روند کراول گروه."""
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM crawl_state WHERE chat_id = ?", (chat_id,))
    return cursor.fetchone()


def update_crawl_state(
    conn: sqlite3.Connection,
    chat_id: int,
    chat_title: str | None = None,
    chat_username: str | None = None,
    oldest_msg_id: int | None = None,
    newest_msg_id: int | None = None,
    total_increment: int = 0,
    status: str = "running",
) -> None:
    """به‌روزرسانی وضعیت و چک‌پوینت کراول برای امکان ادامه (Resume)."""
    conn.execute(
        """
        INSERT INTO crawl_state (
            chat_id, chat_title, chat_username, oldest_msg_id, newest_msg_id,
            total_crawled, last_run_at, status
        ) VALUES (
            ?, ?, ?, ?, ?, ?, datetime('now'), ?
        )
        ON CONFLICT(chat_id) DO UPDATE SET
            chat_title = COALESCE(excluded.chat_title, crawl_state.chat_title),
            chat_username = COALESCE(excluded.chat_username, crawl_state.chat_username),
            oldest_msg_id = CASE
                WHEN crawl_state.oldest_msg_id IS NULL THEN excluded.oldest_msg_id
                WHEN excluded.oldest_msg_id IS NOT NULL THEN MIN(crawl_state.oldest_msg_id, excluded.oldest_msg_id)
                ELSE crawl_state.oldest_msg_id
            END,
            newest_msg_id = CASE
                WHEN crawl_state.newest_msg_id IS NULL THEN excluded.newest_msg_id
                WHEN excluded.newest_msg_id IS NOT NULL THEN MAX(crawl_state.newest_msg_id, excluded.newest_msg_id)
                ELSE crawl_state.newest_msg_id
            END,
            total_crawled = crawl_state.total_crawled + excluded.total_crawled,
            last_run_at = datetime('now'),
            status = excluded.status;
        """,
        (
            chat_id,
            chat_title,
            chat_username,
            oldest_msg_id,
            newest_msg_id,
            total_increment,
            status,
        ),
    )


def get_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """گرفتن آمار دقیق پیام‌ها و انواع مدیا برای گزارش‌گیری."""
    cursor = conn.cursor()
    
    total_messages = cursor.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    total_senders = cursor.execute("SELECT COUNT(DISTINCT sender_id) FROM messages WHERE sender_id IS NOT NULL").fetchone()[0]
    total_replies = cursor.execute("SELECT COUNT(*) FROM messages WHERE reply_to_msg_id IS NOT NULL").fetchone()[0]
    
    date_range = cursor.execute("SELECT MIN(date), MAX(date) FROM messages").fetchone()
    min_date, max_date = (date_range[0], date_range[1]) if date_range else (None, None)
    
    media_breakdown = cursor.execute(
        "SELECT COALESCE(media_type, 'text') as mtype, COUNT(*) as count FROM messages GROUP BY media_type ORDER BY count DESC"
    ).fetchall()
    
    return {
        "total_messages": total_messages,
        "total_senders": total_senders,
        "total_replies": total_replies,
        "first_message_date": min_date,
        "last_message_date": max_date,
        "media_breakdown": {row["mtype"]: row["count"] for row in media_breakdown},
    }


def save_private_message(
    conn: sqlite3.Connection,
    user_id: int,
    role: str,
    text: str,
    user_name: str | None = None,
    user_username: str | None = None,
    message_id: int | None = None,
    reply_to_msg_id: int | None = None,
) -> int:
    """ذخیره پیام گفتگوی خصوصی در جدول مجزای private_conversations."""
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS private_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            user_name TEXT,
            user_username TEXT,
            role TEXT NOT NULL,
            message_id INTEGER,
            reply_to_msg_id INTEGER,
            text TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        """
    )
    cursor.execute(
        """
        INSERT INTO private_conversations (
            user_id, user_name, user_username, role, message_id, reply_to_msg_id, text
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, user_name, user_username, role, message_id, reply_to_msg_id, text),
    )
    return cursor.lastrowid or 0


def get_private_history(
    conn: sqlite3.Connection,
    user_id: int,
    limit: int = 10,
) -> list[dict[str, str]]:
    """واکشی آخرین پیام‌های گفتگوی خصوصی یک کاربر برای بازسازی کانتکست گفتگو."""
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS private_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            user_name TEXT,
            user_username TEXT,
            role TEXT NOT NULL,
            message_id INTEGER,
            reply_to_msg_id INTEGER,
            text TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        """
    )
    cursor.execute(
        """
        SELECT role, text
        FROM private_conversations
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    rows = cursor.fetchall()
    history: list[dict[str, str]] = []
    for r in reversed(rows):
        role_val = r["role"] if isinstance(r, sqlite3.Row) else r[0]
        text_val = r["text"] if isinstance(r, sqlite3.Row) else r[1]
        history.append({
            "role": role_val,
            "content": text_val[:1500],
        })
    return history

