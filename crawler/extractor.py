from __future__ import annotations

import json
from typing import Any
from telethon.tl.types import (
    DocumentAttributeAnimated,
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
    Message,
    MessageMediaContact,
    MessageMediaDocument,
    MessageMediaGeo,
    MessageMediaPhoto,
    MessageMediaPoll,
    MessageMediaWebPage,
    PeerChannel,
    PeerChat,
    PeerUser,
    PhotoSize,
    PhotoSizeProgressive,
    ReactionCustomEmoji,
    ReactionEmoji,
)


def get_sender_info(msg: Message) -> tuple[int | None, str, str | None, str | None]:
    """استخراج مشخصات فرستنده پیام (شناسه، نوع، نام و یوزرنیم)."""
    sender_id = msg.sender_id
    sender_type = "unknown"
    sender_name = None
    sender_username = None

    if isinstance(msg.from_id, PeerUser):
        sender_type = "user"
    elif isinstance(msg.from_id, PeerChannel):
        sender_type = "channel"
    elif isinstance(msg.from_id, PeerChat):
        sender_type = "chat"

    sender = getattr(msg, "sender", None)
    if sender is not None:
        first = getattr(sender, "first_name", "") or ""
        last = getattr(sender, "last_name", "") or ""
        title = getattr(sender, "title", "") or ""
        name = f"{first} {last}".strip() if (first or last) else title
        sender_name = name or None
        sender_username = getattr(sender, "username", None)

    return sender_id, sender_type, sender_name, sender_username


def extract_reactions(msg: Message) -> str | None:
    """استخراج ری‌اکشن‌های ثبت‌شده روی پیام به صورت ساختار JSON."""
    reactions_obj = getattr(msg, "reactions", None)
    if not reactions_obj or not getattr(reactions_obj, "results", None):
        return None

    extracted = []
    for r in reactions_obj.results:
        reaction = r.reaction
        emoji = None
        if isinstance(reaction, ReactionEmoji):
            emoji = reaction.emoticon
        elif isinstance(reaction, ReactionCustomEmoji):
            emoji = f"custom:{reaction.document_id}"
        if emoji:
            extracted.append({"emoji": emoji, "count": r.count})

    return json.dumps(extracted, ensure_ascii=False, default=str) if extracted else None


def extract_media_meta(msg: Message) -> dict[str, Any]:
    """استخراج کامل مشخصات فایل‌های چندرسانه‌ای (بدون دانلود فایل)."""
    meta: dict[str, Any] = {
        "media_type": None,
        "file_id": None,
        "file_unique_id": None,
        "file_name": None,
        "mime_type": None,
        "file_size": None,
        "duration": None,
        "width": None,
        "height": None,
    }

    media = getattr(msg, "media", None)
    if media is None:
        return meta

    # ۱. عکس
    if isinstance(media, MessageMediaPhoto) and media.photo:
        photo = media.photo
        meta["media_type"] = "photo"
        meta["file_id"] = str(photo.id)
        meta["file_unique_id"] = str(photo.access_hash)
        meta["mime_type"] = "image/jpeg"
        
        # پیدا کردن ابعاد بزرگترین نسخه عکس
        sizes = [s for s in getattr(photo, "sizes", []) if isinstance(s, (PhotoSize, PhotoSizeProgressive))]
        if sizes:
            largest = sizes[-1]
            meta["width"] = getattr(largest, "w", None)
            meta["height"] = getattr(largest, "h", None)
            meta["file_size"] = getattr(largest, "size", None)
        return meta

    # ۲. اسناد، ویدئوها، ویس و فایل‌های صوتی
    if isinstance(media, MessageMediaDocument) and media.document:
        doc = media.document
        meta["file_id"] = str(doc.id)
        meta["file_unique_id"] = str(doc.access_hash)
        meta["mime_type"] = doc.mime_type
        meta["file_size"] = doc.size

        # بررسی اتریبیوت‌های سند برای تشخیص نوع دقیق فایل
        is_voice = False
        is_audio = False
        is_video = False
        is_sticker = False
        is_animated = False

        for attr in getattr(doc, "attributes", []):
            if isinstance(attr, DocumentAttributeFilename):
                meta["file_name"] = attr.file_name
            elif isinstance(attr, DocumentAttributeAudio):
                meta["duration"] = attr.duration
                if getattr(attr, "voice", False):
                    is_voice = True
                else:
                    is_audio = True
            elif isinstance(attr, DocumentAttributeVideo):
                is_video = True
                meta["duration"] = attr.duration
                meta["width"] = attr.w
                meta["height"] = attr.h
            elif isinstance(attr, DocumentAttributeSticker):
                is_sticker = True
            elif isinstance(attr, DocumentAttributeAnimated):
                is_animated = True

        if is_voice:
            meta["media_type"] = "voice"
        elif is_video:
            meta["media_type"] = "video"
        elif is_animated:
            meta["media_type"] = "animation"
        elif is_sticker:
            meta["media_type"] = "sticker"
        elif is_audio:
            meta["media_type"] = "audio"
        else:
            mime = (doc.mime_type or "").lower()
            if mime.startswith("video/"):
                meta["media_type"] = "video"
            elif mime.startswith("audio/"):
                meta["media_type"] = "audio"
            else:
                meta["media_type"] = "document"
        return meta

    # ۳. سایر مدیاها
    if isinstance(media, MessageMediaPoll):
        meta["media_type"] = "poll"
    elif isinstance(media, MessageMediaContact):
        meta["media_type"] = "contact"
    elif isinstance(media, MessageMediaGeo):
        meta["media_type"] = "location"
    elif isinstance(media, MessageMediaWebPage):
        meta["media_type"] = "web_page"

    return meta


def extract_message_record(msg: Message, chat_id: int) -> dict[str, Any] | None:
    """تبدیل یک آبجکت پیام Telethon به یک دیکشنری ساختاریافته مطابق با اسکیمای SQLite."""
    # نادیده گرفتن اکشن‌های سیستمی تلگرام مانند ورود/خروج اعضا، تغییر عکس گروه
    if getattr(msg, "action", None) is not None:
        return None

    sender_id, sender_type, sender_name, sender_username = get_sender_info(msg)
    media_meta = extract_media_meta(msg)

    # اطلاعات ریپلای و تاپیک
    reply_to = getattr(msg, "reply_to", None)
    reply_to_msg_id = getattr(reply_to, "reply_to_msg_id", None) if reply_to else None
    reply_to_top_id = getattr(reply_to, "reply_to_top_id", None) if reply_to else None
    is_topic_message = 1 if getattr(reply_to, "forum_topic", False) else 0

    # اطلاعات فوروارد
    fwd = getattr(msg, "fwd_from", None)
    is_forward = 1 if fwd else 0
    forward_from_id = None
    forward_from_name = None
    forward_date = None

    if fwd:
        from_id_peer = getattr(fwd, "from_id", None)
        if isinstance(from_id_peer, PeerUser):
            forward_from_id = from_id_peer.user_id
        elif isinstance(from_id_peer, PeerChannel):
            forward_from_id = from_id_peer.channel_id
        forward_from_name = getattr(fwd, "from_name", None)
        if getattr(fwd, "date", None):
            forward_date = fwd.date.isoformat()

    # اطلاعات تعاملات
    views = getattr(msg, "views", None)
    forwards = getattr(msg, "forwards", None)
    replies_obj = getattr(msg, "replies", None)
    replies_count = getattr(replies_obj, "replies", 0) if replies_obj else 0

    reactions_json = extract_reactions(msg)

    # متادیتای خام اضافی برای موارد پیش‌بینی نشده
    extra_data = {}
    if media_meta["media_type"] == "poll" and getattr(msg.media, "poll", None):
        poll = msg.media.poll
        q = getattr(poll, "question", "")
        extra_data["poll_question"] = getattr(q, "text", str(q)) if q is not None else ""
        if getattr(poll, "answers", None):
            answers = []
            for ans in poll.answers:
                ans_text = getattr(ans, "text", "")
                answers.append(getattr(ans_text, "text", str(ans_text)))
            extra_data["poll_answers"] = answers

    if media_meta["media_type"] == "web_page" and getattr(msg.media, "webpage", None):
        webpage = msg.media.webpage
        extra_data["webpage_url"] = getattr(webpage, "url", None)
        title = getattr(webpage, "title", None)
        extra_data["webpage_title"] = getattr(title, "text", str(title)) if title is not None else None

    raw_json = json.dumps(extra_data, ensure_ascii=False, default=str) if extra_data else None

    return {
        "message_id": msg.id,
        "chat_id": chat_id,
        "sender_id": sender_id,
        "sender_type": sender_type,
        "sender_name": sender_name,
        "sender_username": sender_username,
        "text": msg.message or "",
        "raw_text": getattr(msg, "raw_text", msg.message or ""),
        "reply_to_msg_id": reply_to_msg_id,
        "reply_to_top_id": reply_to_top_id,
        "is_topic_message": is_topic_message,
        "date": msg.date.isoformat(),
        "edit_date": msg.edit_date.isoformat() if getattr(msg, "edit_date", None) else None,
        "is_forward": is_forward,
        "forward_from_id": forward_from_id,
        "forward_from_name": forward_from_name,
        "forward_date": forward_date,
        "media_type": media_meta["media_type"],
        "file_id": media_meta["file_id"],
        "file_unique_id": media_meta["file_unique_id"],
        "file_name": media_meta["file_name"],
        "mime_type": media_meta["mime_type"],
        "file_size": media_meta["file_size"],
        "duration": media_meta["duration"],
        "width": media_meta["width"],
        "height": media_meta["height"],
        "grouped_id": getattr(msg, "grouped_id", None),
        "views": views,
        "forwards": forwards,
        "replies_count": replies_count,
        "reactions_json": reactions_json,
        "raw_json": raw_json,
    }
