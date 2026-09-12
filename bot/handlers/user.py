from __future__ import annotations

import logging
from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest, TelegramMigrateToChat, TelegramRetryAfter
from aiogram.filters import CommandStart, Command
from aiogram.types import Message

from bot.error_logger import report_error
from bot.filters import ShouldRespond
from bot.formatters import escape_html, format_stream_preview
from bot.rag_engine import rag_engine
from bot.rich_telegram import send_rich_draft_stream, send_final_rich_message
from bot.thread_manager import record_message

logger = logging.getLogger("bot.handlers.user")
user_router = Router()


@user_router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    bot_info = await bot.get_me()
    welcome_text = f"""سلام! 👋
من <b>دستیار هوشمند تجربیات گروه موتورسیکلت</b> هستم. 🏍️

تمام گفتگوها، نظرات فنی، تجربیات رفع نقص و توصیه‌های اعضای گروه را در حافظه دارم و می‌توانم به سوالاتت با <b>ذکر دقیق پیام منبع</b> پاسخ دهم.

💡 <b>نحوه استفاده:</b>
• در چت خصوصی (پی‌وی): فقط سوالت را بنویس و بفرست.
• در گروه: روی پیام من ریپلای بزن یا من را منشن کن:
  <code>@{bot_info.username} روغن مناسب برای فصل گرما چیه؟</code>
"""
    try:
        await message.reply(welcome_text, parse_mode="HTML")
    except TelegramMigrateToChat as e:
        logger.info(f"Chat migrated to {e.migrate_to_chat_id}. Retrying cmd_start...")
        await bot.send_message(chat_id=e.migrate_to_chat_id, text=welcome_text, parse_mode="HTML")


@user_router.message(Command("help"))
async def cmd_help(message: Message, bot: Bot):
    bot_info = await bot.get_me()
    help_text = f"""🔍 <b>راهنمای پرسش از ربات:</b>

• برای دریافت بهترین پاسخ، سوال خود را واضح مطرح کنید (مثلاً نام قطعه، کیلومتر کارکرد، یا نشانه خرابی).
• در گروه برای جلوگیری از شلوغی، ربات فقط زمانی پاسخ می‌دهد که <b>منشن</b> شود (<code>@{bot_info.username}</code>) یا روی پیامش <b>ریپلای</b> بزنید.
• در انتهای هر پاسخ، منابع مربوطه در قالب یک <b>نقل‌قول تاشو</b> با لینک مستقیم به پیام‌های گروه درج می‌شوند.
"""
    try:
        await message.reply(help_text, parse_mode="HTML")
    except TelegramMigrateToChat as e:
        logger.info(f"Chat migrated to {e.migrate_to_chat_id}. Retrying cmd_help...")
        await bot.send_message(chat_id=e.migrate_to_chat_id, text=help_text, parse_mode="HTML")


import asyncio
import time
from aiogram import F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import bot_settings

# نگه‌داری تسک‌های در حال اجرای مدل استدلالی برای قابلیت لغو و Answer Now
ACTIVE_TASKS: dict[int, dict] = {}


@user_router.callback_query(F.data.startswith("ansnow_"))
async def handle_answer_now(callback: CallbackQuery, bot: Bot):
    """
    قابلیت مشابه دکمه Answer Now در اپلیکیشن جمینای:
    با کلیک روی این دکمه، استدلال کند و عمیق لغو شده و فوراً پاسخ مستقیم از Fast Model تولید می‌شود.
    تنها کاربری که سوال را پرسیده مجاز به استفاده از این دکمه است.
    """
    parts = callback.data.split("_")
    if len(parts) < 2:
        await callback.answer()
        return

    # استخراج آیدی کاربر سوال‌کننده از داده‌های کالبک در صورت وجود
    asker_id = None
    if len(parts) >= 3 and (parts[2].isdigit() or (parts[2].startswith("-") and parts[2][1:].isdigit())):
        asker_id = int(parts[2])

    current_user_id = callback.from_user.id if callback.from_user else 0

    # اعتبارسنجی اولیه: فقط کاربری که سوال را پرسیده مجاز است
    if asker_id and current_user_id != asker_id:
        await callback.answer(
            "⛔️ این دکمه فقط مخصوص کاربری است که این سوال را مطرح کرده است.",
            show_alert=True,
        )
        return

    placeholder_id_str = parts[1]
    if placeholder_id_str == "pending":
        await callback.answer("⏳ پردازش در حال آغاز است، لطفاً کمی صبر کنید...", show_alert=False)
        return

    try:
        placeholder_id = int(placeholder_id_str)
    except ValueError:
        await callback.answer()
        return

    task_info = ACTIVE_TASKS.get(placeholder_id)

    # اعتبارسنجی ثانویه از دیکشنری تسک‌ها
    if task_info:
        task_asker_id = task_info.get("asker_id")
        if task_asker_id and current_user_id != task_asker_id:
            await callback.answer(
                "⛔️ این دکمه فقط مخصوص کاربری است که این سوال را مطرح کرده است.",
                show_alert=True,
            )
            return

    if not task_info:
        await callback.answer("پاسخ قبلاً ارسال شده یا منقضی گردیده است.", show_alert=False)
        return

    ACTIVE_TASKS.pop(placeholder_id, None)

    # ۱. لغو تسک استدلال جاری
    running_task: asyncio.Task = task_info["task"]
    if not running_task.done():
        running_task.cancel()
        logger.info(f"Reasoning task for msg_id={placeholder_id} cancelled via Answer Now.")

    await callback.answer("⚡ درخواست پاسخ فوری ثبت شد...")

    placeholder = task_info["placeholder"]
    query = task_info["query"]
    sender_name = task_info["sender_name"]
    thread_messages = task_info["thread_messages"]
    orig_message = task_info["message"]

    # ۲. به‌روزرسانی پیام تلگرام به حالت لودینگ سریع
    try:
        await callback.message.edit_text(
            "⚡ <b>در حال تولید پاسخ فوق‌سریع بدون استدلال...</b>",
            reply_markup=None,
            parse_mode="HTML",
        )
    except Exception:
        pass

    # ۳. تولید سریع پاسخ با اسلات Fast همراه با استریم
    last_fast_edit = time.time()

    is_private_fast = callback.message.chat.type == "private"
    fast_draft_id = int(time.time()) % 100000

    async def on_fast_stream(r_text: str, c_text: str):
        nonlocal last_fast_edit
        now = time.time()
        if c_text and (now - last_fast_edit >= 1.1):
            last_fast_edit = now
            if is_private_fast:
                # چت خصوصی: استریم نیتیو Rich Draft
                await send_rich_draft_stream(
                    bot=bot,
                    chat_id=callback.message.chat.id,
                    draft_id=fast_draft_id,
                    reasoning="",
                    content=c_text,
                    can_stop=False,
                )
            else:
                # سوپرگروه: فال‌بک به edit_text
                preview = format_stream_preview("", c_text, is_fast=True)
                try:
                    await callback.message.edit_text(preview, parse_mode="HTML")
                except Exception:
                    pass

    try:
        fast_answer_html = await rag_engine.answer_question(
            question=query,
            sender_name=sender_name,
            thread_messages=thread_messages,
            slot="fast",
            stream_callback=on_fast_stream,
        )
    except Exception as e:
        logger.error(f"Error in fast answer generation: {e}", exc_info=True)
        try:
            await callback.message.edit_text(f"⚠️ خطا در تولید پاسخ فوری: <code>{e}</code>", parse_mode="HTML")
        except Exception:
            pass
        return

    # ۴. تحویل پیام نهایی
    rich_html = getattr(fast_answer_html, "rich_html", fast_answer_html)
    delivered = False

    # تلاش برای ارسال ریچ مسیج نیتیو با جداول و RTL
    try:
        reply_to_id = orig_message.message_id if orig_message else None
        rich_res = await send_final_rich_message(
            bot=bot,
            chat_id=callback.message.chat.id,
            rich_html=rich_html,
            reply_to_message_id=reply_to_id,
        )
        if rich_res:
            try:
                await callback.message.delete()
            except Exception:
                pass
            delivered = True
    except Exception as e:
        logger.debug(f"Fast rich message error: {e}")

    if not delivered:
        try:
            await callback.message.edit_text(
                fast_answer_html,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            record_message(callback.message)
        except Exception:
            try:
                await callback.message.delete()
            except Exception:
                pass
            try:
                sent_msg = await orig_message.reply(
                    fast_answer_html,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                if sent_msg:
                    record_message(sent_msg)
            except Exception:
                await orig_message.reply(fast_answer_html[:4000], parse_mode=None)
        except TelegramMigrateToChat as e:
            sent_msg = await bot.send_message(
                chat_id=e.migrate_to_chat_id,
                text=fast_answer_html,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            if sent_msg:
                record_message(sent_msg)
        except Exception as err:
            await orig_message.reply(fast_answer_html[:4000], parse_mode=None)


@user_router.message(ShouldRespond())
async def handle_rag_question(
    message: Message,
    bot: Bot,
    query: str,
    sender_name: str = "کاربر",
    thread_messages: list[dict[str, str]] | None = None,
):
    """
    پردازش سوال کاربر با موتور تعاملی RAG:
    ۱. ارسال پیام موقت همراه با دکمه شیشه‌ای «⚡ پاسخ فوری (Answer Now)»
    ۲. اجرای فرایند استدلال در قالب یک تسک پس‌زمینه (قابلیت لغو آنی در صورت کلیک دکمه)
    ۳. ارسال پاسخ کامل استدلالی در پایان
    """
    # بررسی حالت پیش‌فرض ربات (reasoning یا fast)
    use_fast_mode = bot_settings.active_mode == "fast"
    asker_id = message.from_user.id if message.from_user else (message.sender_chat.id if message.sender_chat else 0)

    # ارسال ایموجی زمانسنج موقت
    placeholder = None
    try:
        if not use_fast_mode:
            # ایجاد دکمه شیشه‌ای Answer Now
            initial_kb = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⚡ پاسخ فوری (Answer Now)", callback_data=f"ansnow_pending_{asker_id}")]]
            )
            placeholder = await message.reply("⏳", reply_markup=initial_kb)
        else:
            placeholder = await message.reply("⚡")
    except TelegramMigrateToChat as e:
        logger.info(f"Chat migrated to {e.migrate_to_chat_id} during placeholder send.")
        try:
            placeholder = await bot.send_message(chat_id=e.migrate_to_chat_id, text="⏳")
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"Could not send placeholder: {e}")

    # در صورت ساخت موفق placeholder، دکمه را با callback_data درست بروز می‌کنیم
    ans_now_kb = None
    if placeholder and not use_fast_mode:
        ans_now_kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⚡ پاسخ فوری (Answer Now)", callback_data=f"ansnow_{placeholder.message_id}_{asker_id}")]]
        )
        try:
            await placeholder.edit_reply_markup(reply_markup=ans_now_kb)
        except Exception:
            pass

    # ارسال وضعیت در حال تایپ
    target_chat_id = message.chat.id
    try:
        await bot.send_chat_action(chat_id=target_chat_id, action="typing")
    except Exception:
        pass

    last_edit_time = time.time()
    last_stream_time = time.time()
    is_private_chat = message.chat.type == "private"
    stream_draft_id = int(time.time()) % 100000  # شناسه یکتا برای Draft

    async def on_stream(reasoning_text: str, content_text: str):
        nonlocal last_stream_time
        now = time.time()
        if now - last_stream_time < 1.2:
            return
        last_stream_time = now

        if is_private_chat:
            # ─── چت خصوصی: استریم نیتیو Rich Draft با بلوک Thinking ───
            await send_rich_draft_stream(
                bot=bot,
                chat_id=target_chat_id,
                draft_id=stream_draft_id,
                reasoning=reasoning_text,
                content=content_text,
                can_stop=not use_fast_mode and not content_text,
            )
        elif placeholder:
            # ─── سوپرگروه: فال‌بک به edit_text (Draft API پشتیبانی نمی‌شود) ───
            preview_html = format_stream_preview(
                reasoning=reasoning_text,
                content=content_text,
                is_fast=use_fast_mode,
            )
            # دکمه پاسخ فوری تا زمانی که تولید متن اصلی آغاز نشده فعال می‌ماند
            kb = ans_now_kb if (not use_fast_mode and not content_text) else None
            try:
                await placeholder.edit_text(
                    preview_html,
                    reply_markup=kb,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e).lower():
                    pass
            except Exception:
                pass

    async def on_progress(status_text: str):
        nonlocal last_edit_time
        now = time.time()
        if placeholder and (now - last_edit_time >= 1.5):
            last_edit_time = now
            try:
                await placeholder.edit_text(
                    status_text,
                    reply_markup=ans_now_kb if not use_fast_mode else None,
                    parse_mode="HTML",
                )
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e).lower():
                    pass
            except Exception:
                pass

    # تابع پردازش اصلی که در یک تسک قرار می‌گیرد تا قابل Cancel باشد
    async def _run_inference():
        slot_to_use = "fast" if use_fast_mode else "reasoning"
        return await rag_engine.answer_question(
            question=query,
            sender_name=sender_name,
            thread_messages=thread_messages,
            slot=slot_to_use,
            progress_callback=on_progress if not use_fast_mode else None,
            stream_callback=on_stream,
        )

    current_task = asyncio.create_task(_run_inference())

    if placeholder:
        ACTIVE_TASKS[placeholder.message_id] = {
            "task": current_task,
            "bot": bot,
            "message": message,
            "query": query,
            "sender_name": sender_name,
            "thread_messages": thread_messages,
            "placeholder": placeholder,
            "asker_id": asker_id,
        }

    try:
        answer_html = await current_task
    except asyncio.CancelledError:
        logger.info("Main inference task was cancelled (Answer Now triggered). Exiting cleanly.")
        return
    except Exception as rag_err:
        ACTIVE_TASKS.pop(placeholder.message_id if placeholder else 0, None)
        if placeholder:
            try:
                await placeholder.delete()
            except Exception:
                pass

        await report_error(
            bot=bot,
            exception=rag_err,
            message=message,
            context_note=f"خطا در تولید پاسخ RAG برای کوئری: {query}",
        )
        error_reply = (
            "⚠️ <b>خطا در پردازش هوش مصنوعی:</b>\n"
            f"<code>{escape_html(str(rag_err))}</code>\n\n"
            "💡 گزارش خطا برای مدیران ثبت شد. لطفاً مدل یا کلید API را بررسی کنید."
        )
        await message.reply(error_reply, parse_mode="HTML")
        return
    finally:
        ACTIVE_TASKS.pop(placeholder.message_id if placeholder else 0, None)

    # تحویل پاسخ نهایی: ابتدا ارسال با قابلیت جدید Rich Messages (جداول، بخش‌های تاشو نیتیو و RTL)
    rich_html = getattr(answer_html, "rich_html", str(answer_html))
    delivered = False

    try:
        rich_res = await send_final_rich_message(
            bot=bot,
            chat_id=target_chat_id,
            rich_html=rich_html,
            reply_to_message_id=message.message_id,
        )
        if rich_res:
            if placeholder:
                try:
                    await placeholder.delete()
                except Exception:
                    pass
            delivered = True
    except Exception as rich_err:
        logger.debug(f"Could not send rich message: {rich_err}")

    # فال‌بک به ادیت پیام موجود در صورت عدم موفقیت ریچ مسیج
    if not delivered and placeholder:
        try:
            await placeholder.edit_text(
                answer_html,
                reply_markup=None,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            record_message(placeholder)
            delivered = True
        except Exception as edit_err:
            logger.debug(f"Could not edit placeholder to final answer: {edit_err}")
            try:
                await placeholder.delete()
            except Exception:
                pass

    if not delivered:
        try:
            sent_msg = await message.reply(
                answer_html,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            if sent_msg:
                record_message(sent_msg)
        except TelegramMigrateToChat as e:
            logger.info(f"Chat migrated to {e.migrate_to_chat_id} on final answer send.")
            sent_msg = await bot.send_message(
                chat_id=e.migrate_to_chat_id,
                text=answer_html,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            if sent_msg:
                record_message(sent_msg)
        except Exception as send_err:
            await report_error(
                bot=bot,
                exception=send_err,
                message=message,
                context_note="خطا در ارسال پیام نهایی به تلگرام",
            )
            try:
                await message.reply(answer_html[:4000], parse_mode=None)
            except Exception:
                pass
