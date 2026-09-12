from __future__ import annotations

import html
import re

# الگوی جامع شناسایی تگ‌های رفرنس مانند [msg:123] یا [msg:123, msg:456] یا [msg: 123, 456] یا [#123]
MSG_REF_REGEX = re.compile(r"\[(?:(?:msg|message)s?[:\s]|#)\s*([^\]]+)\]", re.IGNORECASE)


def escape_html(text: str) -> str:
    """امن‌سازی رشته متنی برای جلوگیری از خطای پارس HTML تلگرام."""
    return html.escape(text or "")


def format_markdown_tables(text: str) -> str:
    """تبدیل جداول پایپ مارک‌داون به جداول تمیز و تراز شده داخل تگ <pre> تلگرام."""
    table_regex = re.compile(
        r"((?:^[ \t]*\|[^\n]+\|[ \t]*\n)+)",
        re.MULTILINE
    )

    def render_table(match: re.Match) -> str:
        raw_table = match.group(1).strip()
        lines = [line.strip() for line in raw_table.split("\n") if line.strip()]
        if len(lines) < 2:
            return raw_table

        parsed_rows = []
        for line in lines:
            if re.match(r"^\|[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?$", line):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            parsed_rows.append(cells)

        if not parsed_rows:
            return raw_table

        num_cols = max(len(r) for r in parsed_rows)
        normalized_rows = []
        for r in parsed_rows:
            padded = r + [""] * (num_cols - len(r))
            normalized_rows.append(padded)

        col_widths = [max(len(row[i]) for row in normalized_rows) for i in range(num_cols)]

        out_lines = []
        header = " | ".join(normalized_rows[0][i].ljust(col_widths[i]) for i in range(num_cols))
        sep = "-+-".join("-" * col_widths[i] for i in range(num_cols))
        out_lines.append(header)
        out_lines.append(sep)

        for row in normalized_rows[1:]:
            r_str = " | ".join(row[i].ljust(col_widths[i]) for i in range(num_cols))
            out_lines.append(r_str)

        return f"<pre>\n{chr(10).join(out_lines)}\n</pre>"

    return table_regex.sub(render_table, text)


def format_stream_preview(reasoning: str = "", content: str = "", is_fast: bool = False) -> str:
    """قالب‌بندی بهینه و سبک متن در حال استریم برای نمایش زنده همراه با استدلال تاشو."""
    parts = []
    if reasoning and not is_fast:
        lines = [l.strip() for l in reasoning.split("\n") if l.strip()]
        compact = "\n".join(lines[-4:])[:320]
        parts.append(
            f"<blockquote expandable><b>🧠 روند استدلال و تفکر مدل:</b>\n"
            f"<i>{escape_html(compact)}...</i></blockquote>"
        )

    if content:
        # نمایش پاسخ همراه با نشانگر زنده تایپ
        safe_content = markdown_to_telegram_html(content)
        parts.append(f"{safe_content} ▌")
    elif not reasoning:
        parts.append("⚡ <b>در حال تحلیل و تولید پاسخ...</b>")

    return "\n\n".join(parts)


def markdown_to_telegram_html(text: str) -> str:
    """تبدیل جامع و امن مارک‌داون به تگ‌های معتبر و استاندارد HTML تلگرام."""
    if not text:
        return ""

    # ۰. تبدیل جداول مارک‌داون به بلوک‌های <pre> قبل از سایر تبدیلات
    safe = format_markdown_tables(text)

    # ۱. بلوک‌های کد چندخطی: ```lang\ncode\n```
    def replace_code_block(m: re.Match) -> str:
        lang = m.group(1) or ""
        code = m.group(2)
        lang_attr = f' class="language-{lang}"' if lang else ""
        return f"<pre><code{lang_attr}>{html.escape(code.strip())}</code></pre>"

    safe = re.sub(r"```([a-zA-Z0-9_-]*)\n(.*?)```", replace_code_block, safe, flags=re.DOTALL)

    # ۲. کد درون‌خطی: `code`
    safe = re.sub(r"`([^`\n]+)`", lambda m: f"<code>{html.escape(m.group(1))}</code>", safe)

    # ۳. عناوین و تیترها: ### Heading یا ## Heading
    safe = re.sub(r"^\s*#{1,6}\s*(.+)$", r"<b>\1</b>", safe, flags=re.MULTILINE)

    # ۴. نقل‌قول‌های چندخطی مارک‌داون: > quote
    def replace_blockquote(m: re.Match) -> str:
        lines = [re.sub(r"^\s*>\s?", "", l) for l in m.group(0).strip().split("\n")]
        return f"<blockquote>{'\n'.join(lines)}</blockquote>"

    safe = re.sub(r"(?:^\s*>.*\n?)+", replace_blockquote, safe, flags=re.MULTILINE)

    # ۵. لیست‌های مارک‌داون (- یا * در ابتدای خط) به بولت پوینت •
    safe = re.sub(r"^\s*[\*\-]\s+", "• ", safe, flags=re.MULTILINE)

    # ۶. لینک‌های مارک‌داون: [text](https://...)
    safe = re.sub(r"\[([^\]]+)\]\((https?://[^\)]+)\)", r'<a href="\2">\1</a>', safe)

    # ۷. بولد و ایتالیک سه‌تایی: ***متن*** یا ___متن___
    safe = re.sub(r"[\*_]{3}(.+?)[\*_]{3}", r"<b><i>\1</i></b>", safe)

    # ۸. بولد دوتایی: **متن** یا __متن__
    safe = re.sub(r"[\*_]{2}(.+?)[\*_]{2}", r"<b>\1</b>", safe)

    # ۹. خط‌خورده: ~~متن~~
    safe = re.sub(r"~~(.+?)~~", r"<s>\1</s>", safe)

    # ۱۰. پاکسازی کامل ستاره‌ها و آندرلاین‌های باقیمانده ناخواسته
    safe = safe.replace("**", "").replace("__", "")
    safe = re.sub(r"<b>\s*\*+\s*", "<b>", safe)
    safe = re.sub(r"\s*\*+\s*</b>", "</b>", safe)

    return safe




def format_rag_response(
    answer: str,
    message_links: dict[str, str],
    model_name: str | None = None,
    elapsed_time: float | None = None,
    total_inspected: int = 0,
    web_sources_count: int = 0,
) -> str:
    """
    قالب‌بندی مدرن پاسخ هوش مصنوعی مطابق با قابلیت‌های جدید Telegram Bot API 7+:
    - استفاده از نقل‌قول تاشو (Expandable Blockquote) برای تفکیک پیام‌های بررسی‌شده از پیام‌های استفاده‌شده
    - تبدیل تگ‌های [msg:ID] به لینک‌های کلیک‌خور درون متن
    - پانویس شیک و خوانا
    """
    # ۱. تبدیل مارک‌داون خروجی LLM به HTML معتبر تلگرام
    html_body = markdown_to_telegram_html(answer)

    found_msg_ids = set()

    def replace_inline_tag(match: re.Match) -> str:
        content = match.group(1)
        ids = re.findall(r"\b\d+\b", content)
        if not ids:
            return match.group(0)
        links_html = []
        for mid in ids:
            found_msg_ids.add(mid)
            url = message_links.get(mid)
            if url:
                links_html.append(f'<a href="{url}">#{mid}</a>')
            else:
                links_html.append(f"#{mid}")
        return f"[{', '.join(links_html)}]"

    # ۲. تبدیل تگ‌های [msg:ID] به لینک‌های کوتاه کلیک‌خور
    formatted_body = MSG_REF_REGEX.sub(replace_inline_tag, html_body)

    parts = [formatted_body.strip()]

    # ساخت بخش منابع با استفاده از نقل‌قول تاشو (Expandable Blockquote) تلگرام
    if found_msg_ids or message_links or total_inspected > 0:
        # پیام‌هایی که مستقیماً در متن رفرنس داده شدند و لینک معتبر دارند
        used_ids = sorted(
            [mid for mid in found_msg_ids if message_links.get(mid)],
            key=lambda x: int(x) if x.isdigit() else 0
        )
        if not used_ids:
            used_ids = sorted(
                [mid for mid, url in message_links.items() if url][:6],
                key=lambda x: int(x) if x.isdigit() else 0
            )

        sources_lines = ["<blockquote expandable><b>🔍 ارزیابی و استناد فنی سوابق:</b>"]
        if total_inspected > 0:
            sources_lines.append(f"• تعداد پیام‌های بررسی‌شده در آرشیو گروه: <b>{total_inspected} پیام</b>")
        if web_sources_count > 0:
            sources_lines.append(f"• استعلام زنده از وب و کاتالوگ کارخانه: <b>{web_sources_count} مرجع</b>")

        if used_ids:
            sources_lines.append("<b>📌 پیام‌های استفاده‌شده در این تحلیل:</b>")
            for mid in used_ids:
                url = message_links.get(str(mid))
                if url:
                    sources_lines.append(f'• <a href="{url}">مشاهده پیام #{mid} در تلگرام</a>')

        sources_lines.append("</blockquote>")
        parts.append("\n".join(sources_lines))

    # پاورقی کوچک حاوی مدل و زمان
    footer_items = []
    if model_name:
        footer_items.append(f"⚡ <code>{escape_html(model_name)}</code>")
    if elapsed_time is not None:
        footer_items.append(f"⏱ <code>{elapsed_time:.1f}s</code>")

    if footer_items:
        parts.append(f"<tg-spoiler>{' | '.join(footer_items)}</tg-spoiler>")

    return "\n\n".join(parts)


def markdown_tables_to_rich_html(text: str) -> str:
    """تبدیل جداول مارک‌داون به جداول نیتیو HTML تلگرام (RichBlockTable / <table>)."""
    lines = text.split("\n")
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("|") and line.strip().endswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|") and lines[i].strip().endswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            if len(table_lines) >= 2 and any("-" in cell for cell in table_lines[1].split("|")):
                rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in table_lines]
                header = rows[0]
                data_rows = rows[2:] if len(rows) > 2 else []
                t_html = ['<table border="1">']
                t_html.append("  <tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in header) + "</tr>")
                for r in data_rows:
                    t_html.append("  <tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>")
                t_html.append("</table>")
                out.append("\n".join(t_html))
            else:
                out.extend(table_lines)
        else:
            out.append(line)
            i += 1
    return "\n".join(out)


def format_rag_rich_html(
    answer: str,
    message_links: dict[str, str],
    model_name: str | None = None,
    elapsed_time: float | None = None,
    total_inspected: int = 0,
    web_sources_count: int = 0,
) -> str:
    """
    قالب‌بندی فوق‌مدرن پاسخ RAG برای ارسال با متد جدید sendRichMessage تلگرام:
    - جداول واقعی با تگ <table>
    - بخش‌های تاشو با تگ نیتیو <details><summary>
    - تراز راست‌به‌چپ نیتیو (RTL)
    - سرفصل‌های معتبر <h2> و <h3>
    """
    # ۱. ابتدا جداول مارک‌داون را به جدول نیتیو HTML تبدیل می‌کنیم
    text_with_tables = markdown_tables_to_rich_html(answer)

    # ۲. تبدیل تیترها و استایل‌های متنی
    # هدینگ‌های ۲ و ۳
    text_with_tables = re.sub(r"^\s*###\s*(.+)$", r"<h3>\1</h3>", text_with_tables, flags=re.MULTILINE)
    text_with_tables = re.sub(r"^\s*##\s*(.+)$", r"<h2>\1</h2>", text_with_tables, flags=re.MULTILINE)
    text_with_tables = re.sub(r"^\s*#\s*(.+)$", r"<h1>\1</h1>", text_with_tables, flags=re.MULTILINE)

    # بولد و ایتالیک و کد
    text_with_tables = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text_with_tables)
    text_with_tables = re.sub(r"[\*_]{2}(.+?)[\*_]{2}", r"<b>\1</b>", text_with_tables)
    text_with_tables = re.sub(r"(?<!\*)\*([^\*\n]+)\*(?!\*)", r"<i>\1</i>", text_with_tables)

    # لیست‌ها (- یا * در ابتدای سطر) به بولت •
    text_with_tables = re.sub(r"^\s*[\*\-]\s+", "• ", text_with_tables, flags=re.MULTILINE)

    # نقل‌قول‌ها
    def replace_blockquote(m: re.Match) -> str:
        lines = [re.sub(r"^\s*>\s?", "", l) for l in m.group(0).strip().split("\n")]
        return f"<blockquote>{'\n'.join(lines)}</blockquote>"

    text_with_tables = re.sub(r"(?:^\s*>.*\n?)+", replace_blockquote, text_with_tables, flags=re.MULTILINE)

    # پاکسازی ستاره‌های باقیمانده
    text_with_tables = text_with_tables.replace("**", "").replace("__", "")

    found_msg_ids = set()

    def replace_inline_tag(match: re.Match) -> str:
        content = match.group(1)
        ids = re.findall(r"\b\d+\b", content)
        if not ids:
            return match.group(0)
        links_html = []
        for mid in ids:
            found_msg_ids.add(mid)
            url = message_links.get(mid)
            if url:
                links_html.append(f'<a href="{url}">#{mid}</a>')
            else:
                links_html.append(f"#{mid}")
        return f"[{', '.join(links_html)}]"

    formatted_body = MSG_REF_REGEX.sub(replace_inline_tag, text_with_tables)

    parts = [formatted_body.strip()]

    # بخش منابع با تگ نیتیو <details>
    if found_msg_ids or message_links or total_inspected > 0:
        used_ids = sorted(
            [mid for mid in found_msg_ids if message_links.get(mid)],
            key=lambda x: int(x) if x.isdigit() else 0
        )
        if not used_ids:
            used_ids = sorted(
                [mid for mid, url in message_links.items() if url][:6],
                key=lambda x: int(x) if x.isdigit() else 0
            )

        details_lines = ["<details>", "  <summary>🔍 <b>ارزیابی و استناد فنی سوابق</b></summary>"]
        if total_inspected > 0:
            details_lines.append(f"  <p>• تعداد پیام‌های بررسی‌شده در آرشیو گروه: <b>{total_inspected} پیام</b></p>")
        if web_sources_count > 0:
            details_lines.append(f"  <p>• استعلام زنده از وب و کاتالوگ کارخانه: <b>{web_sources_count} مرجع</b></p>")

        if used_ids:
            details_lines.append("  <p><b>📌 پیام‌های استفاده‌شده در این تحلیل:</b></p><ul>")
            for mid in used_ids:
                url = message_links.get(str(mid))
                if url:
                    details_lines.append(f'    <li><a href="{url}">مشاهده پیام #{mid} در تلگرام</a></li>')
            details_lines.append("  </ul>")

        details_lines.append("</details>")
        parts.append("\n".join(details_lines))

    footer_items = []
    if model_name:
        footer_items.append(f"⚡ <code>{escape_html(model_name)}</code>")
    if elapsed_time is not None:
        footer_items.append(f"⏱ <code>{elapsed_time:.1f}s</code>")

    if footer_items:
        parts.append(f"<tg-spoiler>{' | '.join(footer_items)}</tg-spoiler>")

    return "\n\n".join(parts)

