from __future__ import annotations

import re
import sqlite3
import sys
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

console = Console()

# لیست کلمات صرفاً تعارفاتی و احوالپرسی خالی
GREETINGS = {
    "سلام", "سلام علیکم", "سلام عليكم", "درود", "درودها", "صبح بخیر", "شب بخیر", "عصر بخیر", "روز بخیر",
    "ممنون", "مرسی", "دمت گرم", "فدات", "قربانت", "چاکرم", "نوکرم", "دستت درد نکنه", "دستتون درد نکنه",
    "خواهش میکنم", "خواهش", "تشکر", "خیلی ممنون", "سپاس", "سپاسگزارم", "عزیزمی", "ارادت", "ارادتمند",
    "بله", "خیر", "اوکی", "ok", "باشه", "tnx", "thanks", "tanks", "salam", "slm", "bye", "خداحافظ"
}

# بررسی اینکه آیا متن فقط ایموجی/علائم است و هیچ حرف یا عددی ندارد
ALPHANUMERIC_REGEX = re.compile(r"[a-zA-Z0-9\u0600-\u06FF\uFB8A\u067E\u0686\u06AF]")


def is_pure_emoji(text: str) -> bool:
    if not text:
        return False
    return not bool(ALPHANUMERIC_REGEX.search(text))


def ensure_classification_columns(conn: sqlite3.Connection) -> None:
    """افزودن ستون‌های رده‌بندی به جدول messages در صورت عدم وجود (بدون حذف داده)."""
    cursor = conn.cursor()
    cols = {row[1] for row in cursor.execute("PRAGMA table_info(messages)").fetchall()}

    if "content_category" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN content_category TEXT DEFAULT 'valuable_text'")
    if "is_embeddable" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN is_embeddable INTEGER DEFAULT 1")
    
    # ایندکس برای دسترسی سریع به پیام‌های قابل امبدینگ
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_embeddable ON messages(is_embeddable, content_category)")


def classify_message(
    text: str | None,
    media_type: str | None,
    reply_to_msg_id: int | None,
) -> tuple[str, int]:
    """
    تعیین رده معنایی پیام و قابلیت امبد شدن آن.
    خروجی: (content_category, is_embeddable)
    """
    clean_text = (text or "").strip()
    is_reply = reply_to_msg_id is not None

    # ۱. استیکرها (فاقد ارزش متنی مستقل برای امبدینگ)
    if media_type == "sticker":
        return "sticker", 0

    # ۲. پیام‌های صوتی بدون متن (فعلاً تا قبل از فاز Speech-to-Text قابل امبد نیستند ولی حفظ می‌شوند)
    if media_type == "voice" and not clean_text:
        return "voice_no_text", 0

    # ۳. مدیاهای بدون هیچ‌گونه کپشن یا متن (عکس، فیلم، سند خام)
    if media_type is not None and not clean_text:
        if is_reply:
            return "media_reply_no_text", 0
        return "media_standalone_no_text", 0

    # ۴. پیام‌های متنی کاملاً خالی (یا فقط فاصله‌های خالی)
    if not clean_text:
        return "empty_text", 0

    # ۵. پیام‌های صرفاً ایموجی (بدون هیچ حرف یا عدد)
    if is_pure_emoji(clean_text):
        return "pure_emoji", 0

    lower_text = clean_text.lower()

    # ۶. تعارفات و احوالپرسی‌های صرف
    if lower_text in GREETINGS:
        if is_reply:
            return "greeting_reply", 0
        return "greeting_standalone", 0

    # ۷. پیام‌های بسیار کوتاه (زیر ۱۵ کاراکتر)
    if len(clean_text) <= 15:
        if is_reply:
            # بسیار مهم: پاسخ‌های فنی تک کلمه‌ای مثل "10w40"، "انجیکی"، "روغن ترمز"
            return "short_reply_answer", 1
        else:
            # پیام‌های کوتاه بدون ریپلای (اغلب بخشی از جمله تقطیع‌شده یا سوالات کوتاه)
            return "short_standalone", 1

    # ۸. پیام‌های تصویری یا ویدئویی دارای متن و توضیح
    if media_type is not None and clean_text:
        return "media_with_caption", 1

    # ۹. پیام‌های متنی استاندارد و غنی
    return "valuable_text", 1


def run_classification() -> dict[str, Any]:
    """اجرای دسته‌بندی روی تمام رکوردهای دیتابیس و نمایش آمار تفکیکی."""
    init_db()

    with get_db() as conn:
        ensure_classification_columns(conn)

        console.print("[cyan]در حال خواندن تمام پیام‌ها از دیتابیس...[/cyan]")
        cursor = conn.cursor()
        cursor.execute("SELECT id, text, media_type, reply_to_msg_id FROM messages")
        rows = cursor.fetchall()

        updates = []
        stats: dict[str, int] = {}
        embeddable_count = 0
        non_embeddable_count = 0

        for r in rows:
            category, is_emb = classify_message(
                text=r["text"],
                media_type=r["media_type"],
                reply_to_msg_id=r["reply_to_msg_id"],
            )
            updates.append((category, is_emb, r["id"]))
            stats[category] = stats.get(category, 0) + 1
            if is_emb == 1:
                embeddable_count += 1
            else:
                non_embeddable_count += 1

        console.print(f"[cyan]در حال به‌روزرسانی برچسب‌ها برای {len(updates):,} پیام...[/cyan]")
        conn.executemany(
            "UPDATE messages SET content_category = ?, is_embeddable = ? WHERE id = ?",
            updates,
        )

    return {
        "total": len(rows),
        "embeddable": embeddable_count,
        "non_embeddable": non_embeddable_count,
        "categories": stats,
    }


def print_classification_report(result: dict[str, Any]) -> None:
    """نمایش زیبای گزارش رده‌بندی و تفکیک پیام‌ها."""
    total = result["total"]
    emb = result["embeddable"]
    non_emb = result["non_embeddable"]

    summary_table = Table(title="🏷️ خلاصه تفکیک ارزش پیام‌ها برای RAG (بدون حذف داده)", style="bold cyan")
    summary_table.add_column("دسته‌بندی کلی", style="white")
    summary_table.add_column("تعداد", style="bold yellow")
    summary_table.add_column("درصد از کل", style="bold green")
    summary_table.add_column("وضعیت در امبدینگ", style="bold magenta")

    summary_table.add_row(
        "پیام‌های باارزش و قابل امبد (شامل جواب‌های کوتاه ریپلای)",
        f"{emb:,}",
        f"{(emb / total) * 100:.1f}%",
        "✓ وارد ساخت تردهای RAG می‌شود",
    )
    summary_table.add_row(
        "پیام‌های نویز / فاقد متن (عکس خام، استیکر، ایموجی، تعارفات)",
        f"{non_emb:,}",
        f"{(non_emb / total) * 100:.1f}%",
        "✗ امبد نمی‌شود (در دیتابیس محفوظ است)",
    )
    summary_table.add_row("مجموع کل پیام‌های دیتابیس", f"{total:,}", "100%", "-")

    console.print(summary_table)

    # جدول تفکیک دقیق
    cat_names = {
        "valuable_text": "متن کامل و معنادار (> ۱۵ کاراکتر)",
        "short_reply_answer": "پاسخ‌های فنی کوتاه در ریپلای (مثل 10w40، نام قطعه)",
        "short_standalone": "پیام کوتاه مستقل (سوال کوتاه یا جمله ناقص)",
        "media_with_caption": "عکس/ویدئو دارای کپشن و توضیحات فنی",
        "media_standalone_no_text": "عکس/ویدئو بدون کپشن و بدون ریپلای",
        "media_reply_no_text": "عکس/ویدئو ارسال‌شده در پاسخ به سوال (بدون متن)",
        "pure_emoji": "صرفاً ایموجی خالی (بدون متن)",
        "greeting_reply": "تعارف و تشکر در ریپلای (ممنون، دمت گرم)",
        "greeting_standalone": "سلام و احوالپرسی خالی بدون ریپلای",
        "voice_no_text": "پیام صوتی / ویس (بدون متن نوشتاری)",
        "sticker": "استیکر تلگرام",
        "empty_text": "پیام کاملاً خالی یا بدون کاراکتر",
    }

    detail_table = Table(title="📊 جزئیات تفکیک ۱۲ گروه محتوایی", style="bold blue")
    detail_table.add_column("نوع محتوا", style="white")
    detail_table.add_column("تعداد", style="yellow")
    detail_table.add_column("درصد", style="cyan")
    detail_table.add_column("قابلیت امبد (is_embeddable)", style="magenta")

    for cat, count in sorted(result["categories"].items(), key=lambda x: x[1], reverse=True):
        is_emb_label = "بله (۱)" if cat in {"valuable_text", "short_reply_answer", "short_standalone", "media_with_caption"} else "خیر (۰)"
        detail_table.add_row(
            cat_names.get(cat, cat),
            f"{count:,}",
            f"{(count / total) * 100:.2f}%",
            is_emb_label,
        )

    console.print(detail_table)


if __name__ == "__main__":
    res = run_classification()
    print_classification_report(res)
