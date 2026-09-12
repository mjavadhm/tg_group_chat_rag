from __future__ import annotations

import html
import logging
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.config import DEFAULT_SYSTEM_PROMPT, bot_settings, set_setting
from bot.filters import IsAdmin
from bot.formatters import escape_html
from bot.providers import ProviderRegistry, mask_key
from core.db import get_db
from core.repo import get_stats

logger = logging.getLogger("bot.handlers.admin")
admin_router = Router()
admin_router.message.filter(IsAdmin())


# --- وضعیت‌های ماشین حالت محدود (FSM States) ---

class AddProviderSG(StatesGroup):
    waiting_for_id = State()
    waiting_for_name = State()
    waiting_for_base_url = State()
    waiting_for_initial_model = State()


class AddKeySG(StatesGroup):
    waiting_for_name = State()
    waiting_for_key = State()


class AddModelSG(StatesGroup):
    waiting_for_model = State()


class EditProviderSG(StatesGroup):
    waiting_for_name = State()
    waiting_for_base_url = State()


class PromptEditSG(StatesGroup):
    waiting_for_prompt = State()


class TopKEditSG(StatesGroup):
    waiting_for_k = State()


class ErrorChannelSG(StatesGroup):
    waiting_for_channel = State()


# دکمه مشترک انصراف
CANCEL_KB = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="❌ انصراف و بازگشت", callback_data="fsm_cancel")]]
)


@admin_router.callback_query(F.data == "fsm_cancel")
async def handle_fsm_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("عملیات لغو شد.")
    await show_main_config(callback.message, edit=True)


@admin_router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.reply("✓ عملیات جاری لغو شد.")
    await show_main_config(message, edit=False)


# ==============================================================================
# ۱. داشبورد اصلی تنظیمات (/config)
# ==============================================================================

def build_main_config_keyboard() -> InlineKeyboardMarkup:
    r_prov, r_model, _, _ = ProviderRegistry.get_reasoning_slot()
    f_prov, f_model, _, _ = ProviderRegistry.get_fast_slot()
    mode_emoji = "🧠 استدلالی" if bot_settings.active_mode == "reasoning" else "⚡ فوق‌سریع"
    ingest_status = "🟢 روشن" if bot_settings.enable_live_ingest else "🔴 خاموش"
    tools_status = "🟢 روشن" if bot_settings.enable_tools else "🔴 خاموش"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎛 مدیریت پروایدرها و مدل‌ها", callback_data="cfg_provs"),
            ],
            [
                InlineKeyboardButton(text=f"🛠 مدیریت ابزارهای هوش مصنوعی: {tools_status}", callback_data="cfg_tools"),
            ],
            [
                InlineKeyboardButton(text=f"🎯 حالت پاسخگویی: {mode_emoji}", callback_data="t_mode"),
            ],
            [
                InlineKeyboardButton(text=f"📥 امبد زنده پیام‌های گروه: {ingest_status}", callback_data="t_i"),
            ],
            [
                InlineKeyboardButton(text=f"📚 تنظیم Top-K ({bot_settings.rag_top_k} منبع)", callback_data="cfg_topk"),
                InlineKeyboardButton(text="🚨 کانال خطاها", callback_data="cfg_errch"),
            ],
            [
                InlineKeyboardButton(text="📜 مشاهده و ویرایش پرامپت سیستم", callback_data="cfg_prompt"),
            ],
            [
                InlineKeyboardButton(text="🔄 بروزرسانی داشبورد", callback_data="cfg_refresh"),
            ],
        ]
    )


def format_main_config_text() -> str:
    r_prov, r_model, _, _ = ProviderRegistry.get_reasoning_slot()
    f_prov, f_model, _, _ = ProviderRegistry.get_fast_slot()
    prov_r = ProviderRegistry.get_provider(r_prov)
    prov_f = ProviderRegistry.get_provider(f_prov)
    r_name = prov_r.get("name", r_prov) if prov_r else r_prov
    f_name = prov_f.get("name", f_prov) if prov_f else f_prov

    # شمارش ابزارهای فعال
    active_tools_count = sum([
        1 if bot_settings.enable_tool_group_search else 0,
        1 if bot_settings.enable_tool_surrounding else 0,
        1 if bot_settings.enable_tool_web_search else 0,
    ])

    err_ch = bot_settings.error_channel_id or "❌ ثبت نشده"
    is_custom_prompt = bot_settings.system_prompt != DEFAULT_SYSTEM_PROMPT

    return f"""⚙️ <b>داشبورد جامع مدیریت ربات مهندسی RAG</b>

🧠 <b>اسلات استدلالی (Reasoning):</b>
• پروایدر: <b>{escape_html(r_name)}</b>
• مدل: <code>{escape_html(r_model)}</code>

⚡ <b>اسلات پاسخ سریع (Fast):</b>
• پروایدر: <b>{escape_html(f_name)}</b>
• مدل: <code>{escape_html(f_model)}</code>

🎯 <b>حالت پیش‌فرض پاسخ:</b> <b>{bot_settings.active_mode}</b>
🛠 <b>وضعیت ابزارهای فعال:</b> <b>{active_tools_count} از ۳ ابزار</b> (کلید اصلی: {"روشن" if bot_settings.enable_tools else "خاموش"})
📥 <b>امبد زنده پیام‌های ورودی:</b> {"🟢 فعال" if bot_settings.enable_live_ingest else "🔴 غیرفعال"}
📚 <b>سقف تردهای ارسالی (Top-K):</b> <b>{bot_settings.rag_top_k}</b> منبع
🚨 <b>کانال ثبت خطاها:</b> <code>{escape_html(str(err_ch))}</code>
📜 <b>وضعیت پرامپت:</b> {"✏️ سفارشی" if is_custom_prompt else "🛡️ معماری سه‌گانه پیش‌فرض"}

<i>جهت اعمال تغییرات، روی هر بخش لمس کنید:</i>"""


async def show_main_config(message_or_target, edit: bool = False):
    text = format_main_config_text()
    kb = build_main_config_keyboard()
    if edit and hasattr(message_or_target, "edit_text"):
        try:
            await message_or_target.edit_text(text, reply_markup=kb, parse_mode="HTML")
            return
        except Exception:
            pass
    if hasattr(message_or_target, "reply"):
        await message_or_target.reply(text, reply_markup=kb, parse_mode="HTML")
    elif hasattr(message_or_target, "send_message"):
        await message_or_target.send_message(text=text, reply_markup=kb, parse_mode="HTML")


@admin_router.message(Command("config"))
async def cmd_config(message: Message, state: FSMContext):
    await state.clear()
    await show_main_config(message, edit=False)


@admin_router.callback_query(F.data == "cfg_main")
async def cb_cfg_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_main_config(callback.message, edit=True)


@admin_router.callback_query(F.data == "cfg_refresh")
async def cb_cfg_refresh(callback: CallbackQuery):
    await callback.answer("داشبورد بروزرسانی شد.")
    await show_main_config(callback.message, edit=True)


# ==============================================================================
# ۲. پنل مدیریت ابزارها (Tools / MCP)
# ==============================================================================

def build_tools_keyboard() -> InlineKeyboardMarkup:
    master = "🟢 روشن" if bot_settings.enable_tools else "🔴 خاموش"
    group = "🟢 روشن" if bot_settings.enable_tool_group_search else "🔴 خاموش"
    surr = "🟢 روشن" if bot_settings.enable_tool_surrounding else "🔴 خاموش"
    web = "🟢 روشن" if bot_settings.enable_tool_web_search else "🔴 خاموش"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"🛠 کلید اصلی ابزارها (Master Switch): {master}", callback_data="t_m"),
            ],
            [
                InlineKeyboardButton(text=f"🔍 جستجو در تجربیات گروه: {group}", callback_data="t_g"),
            ],
            [
                InlineKeyboardButton(text=f"🪟 پنجره پیام‌های مجاور (Context): {surr}", callback_data="t_s"),
            ],
            [
                InlineKeyboardButton(text=f"🌐 استعلام وب و ویدیوهای یوتیوب: {web}", callback_data="t_w"),
            ],
            [
                InlineKeyboardButton(text="🔙 بازگشت به تنظیمات اصلی", callback_data="cfg_main"),
            ],
        ]
    )


def format_tools_text() -> str:
    return """🛠 <b>مدیریت ابزارهای هوش مصنوعی (Agentic Tools & MCP)</b>

در این بخش می‌توانید مشخص کنید کدام ابزارها در اختیار مدل هوش مصنوعی باشند تا در صورت نیاز به طور خودکار آن‌ها را صدا بزند:

۱. <b>جستجو در گروه (search_group_chat):</b> جستجوی معنایی سوابق اعضای گروه، راهکارهای حل خرابی، روغن و مکانیک‌ها.
۲. <b>پیام‌های مجاور (get_surrounding_messages):</b> خواندن چند پیام قبل و بعد یک پیام جهت درک کامل زمینه گفتگو.
۳. <b>وب و یوتیوب (search_web):</b> جستجوی زنده برای منوال رسمی کارخانه (OEM Manual)، مشخصات پارت نامبر و ویدیوهای آموزشی.

<i>با لمس هر کلید، وضعیت آن بلافاصله تغییر می‌کند:</i>"""


@admin_router.callback_query(F.data == "cfg_tools")
async def cb_open_tools(callback: CallbackQuery):
    await callback.message.edit_text(format_tools_text(), reply_markup=build_tools_keyboard(), parse_mode="HTML")


@admin_router.callback_query(F.data.in_({"t_m", "t_g", "t_s", "t_w", "t_i", "t_mode"}))
async def handle_toggle_switches(callback: CallbackQuery):
    data = callback.data
    if data == "t_m":
        new_val = "0" if bot_settings.enable_tools else "1"
        set_setting("ENABLE_TOOLS", new_val)
        await callback.answer(f"کلید اصلی ابزارها {'روشن' if new_val == '1' else 'خاموش'} شد.")
        await callback.message.edit_reply_markup(reply_markup=build_tools_keyboard())
    elif data == "t_g":
        new_val = "0" if bot_settings.enable_tool_group_search else "1"
        set_setting("ENABLE_TOOL_GROUP_SEARCH", new_val)
        await callback.answer(f"جستجو در گروه {'روشن' if new_val == '1' else 'خاموش'} شد.")
        await callback.message.edit_reply_markup(reply_markup=build_tools_keyboard())
    elif data == "t_s":
        new_val = "0" if bot_settings.enable_tool_surrounding else "1"
        set_setting("ENABLE_TOOL_SURROUNDING", new_val)
        await callback.answer(f"پیام‌های مجاور {'روشن' if new_val == '1' else 'خاموش'} شد.")
        await callback.message.edit_reply_markup(reply_markup=build_tools_keyboard())
    elif data == "t_w":
        new_val = "0" if bot_settings.enable_tool_web_search else "1"
        set_setting("ENABLE_TOOL_WEB_SEARCH", new_val)
        await callback.answer(f"وب و ویدیو {'روشن' if new_val == '1' else 'خاموش'} شد.")
        await callback.message.edit_reply_markup(reply_markup=build_tools_keyboard())
    elif data == "t_i":
        new_val = "0" if bot_settings.enable_live_ingest else "1"
        set_setting("ENABLE_LIVE_INGEST", new_val)
        await callback.answer(f"امبد زنده پیام‌ها {'روشن' if new_val == '1' else 'خاموش'} شد.")
        await show_main_config(callback.message, edit=True)
    elif data == "t_mode":
        new_mode = "fast" if bot_settings.active_mode == "reasoning" else "reasoning"
        set_setting("ACTIVE_MODE", new_mode)
        await callback.answer(f"حالت پیش‌فرض به {new_mode} تغییر یافت.")
        await show_main_config(callback.message, edit=True)


# ==============================================================================
# ۳. پنل لیست پروایدرها (Providers Overview)
# ==============================================================================

def build_providers_list_keyboard() -> InlineKeyboardMarkup:
    providers = ProviderRegistry.get_providers()
    buttons = []

    # دکمه‌های تک‌تک پروایدرها به صورت جفتی
    row = []
    for pid, pdata in providers.items():
        name = pdata.get("name", pid)
        # کوتاه کردن نام در صورت لزوم
        label = name if len(name) <= 24 else name[:22] + ".."
        row.append(InlineKeyboardButton(text=label, callback_data=f"pv_o_{pid}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # دکمه‌های عملیاتی
    buttons.append([
        InlineKeyboardButton(text="➕ افزودن پروایدر جدید", callback_data="pv_start_add"),
    ])
    buttons.append([
        InlineKeyboardButton(text="🧠 تعیین مدل استدلالی", callback_data="pv_sl_r"),
        InlineKeyboardButton(text="⚡ تعیین مدل سریع", callback_data="pv_sl_f"),
    ])
    buttons.append([
        InlineKeyboardButton(text="🔙 بازگشت به تنظیمات اصلی", callback_data="cfg_main"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def format_providers_list_text() -> str:
    r_prov, r_model, _, _ = ProviderRegistry.get_reasoning_slot()
    f_prov, f_model, _, _ = ProviderRegistry.get_fast_slot()
    providers = ProviderRegistry.get_providers()

    lines = [
        "🎛 <b>لیست پروایدرهای متصل به سیستم</b>\n",
        f"🧠 <b>اسلات استدلالی:</b> <code>{escape_html(r_model)}</code> ({escape_html(r_prov)})",
        f"⚡ <b>اسلات پرسرعت:</b> <code>{escape_html(f_model)}</code> ({escape_html(f_prov)})\n",
        "👇 <i>برای مشاهده، ویرایش، افزودن کلید یا مدل، روی پروایدر مورد نظر بزنید:</i>",
    ]
    return "\n".join(lines)


@admin_router.callback_query(F.data == "cfg_provs")
@admin_router.message(Command("providers", "models"))
async def show_providers_list(event: Message | CallbackQuery, state: FSMContext | None = None):
    if state:
        await state.clear()
    msg = event if isinstance(event, Message) else event.message
    edit = isinstance(event, CallbackQuery)
    text = format_providers_list_text()
    kb = build_providers_list_keyboard()
    if edit:
        try:
            await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
            return
        except Exception:
            pass
    await msg.reply(text, reply_markup=kb, parse_mode="HTML")


# ==============================================================================
# ۴. پنل اختصاصی یک پروایدر (Single Provider Details & Actions)
# ==============================================================================

def build_provider_detail_keyboard(pid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ افزودن مدل", callback_data=f"pv_adm_{pid}"),
                InlineKeyboardButton(text="➖ حذف مدل", callback_data=f"pv_dlm_{pid}"),
            ],
            [
                InlineKeyboardButton(text="🔑 افزودن کلید API", callback_data=f"pv_adk_{pid}"),
                InlineKeyboardButton(text="🗑 حذف کلید", callback_data=f"pv_dlk_{pid}"),
            ],
            [
                InlineKeyboardButton(text="⭐ تعیین کلید پیش‌فرض", callback_data=f"pv_sdk_{pid}"),
            ],
            [
                InlineKeyboardButton(text="✏️ تغییر نام", callback_data=f"pv_en_{pid}"),
                InlineKeyboardButton(text="🔗 تغییر Base URL", callback_data=f"pv_eu_{pid}"),
            ],
            [
                InlineKeyboardButton(text="❌ حذف کامل این پروایدر", callback_data=f"pv_cdp_{pid}"),
            ],
            [
                InlineKeyboardButton(text="🔙 بازگشت به لیست پروایدرها", callback_data="cfg_provs"),
            ],
        ]
    )


def format_provider_detail_text(pid: str) -> str:
    prov = ProviderRegistry.get_provider(pid)
    if not prov:
        return f"❌ پروایدر با شناسه <code>{escape_html(pid)}</code> یافت نشد."

    name = prov.get("name", pid)
    base_url = prov.get("base_url", "")
    keys = prov.get("api_keys", [])
    models = prov.get("models", [])

    keys_lines = []
    if not keys:
        keys_lines.append("<i>• هیچ کلیدی ثبت نشده است.</i>")
    else:
        for k in keys:
            k_name = k.get("name", "کلید")
            k_masked = mask_key(k.get("key", ""))
            star = " ⭐ <b>(پیش‌فرض)</b>" if k.get("is_default") else ""
            keys_lines.append(f"• <b>{escape_html(k_name)}:</b> <code>{k_masked}</code>{star}")

    models_lines = []
    if not models:
        models_lines.append("<i>• هیچ مدلی ثبت نشده است.</i>")
    else:
        for m in models:
            models_lines.append(f"• <code>{escape_html(m)}</code>")

    return f"""🏢 <b>مدیریت پروایدر {escape_html(name)}</b>

🆔 <b>شناسه:</b> <code>{escape_html(pid)}</code>
🌐 <b>آدرس Base URL:</b>
<code>{escape_html(base_url)}</code>

🔑 <b>کلیدهای API ({len(keys)} کلید):</b>
{chr(10).join(keys_lines)}

🤖 <b>مدل‌های تعریف‌شده ({len(models)} مدل):</b>
{chr(10).join(models_lines)}

<i>از گزینه‌های زیر برای ویرایش استفاده کنید:</i>"""


@admin_router.callback_query(F.data.startswith("pv_o_"))
async def cb_open_provider_detail(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    pid = callback.data.replace("pv_o_", "").strip()
    text = format_provider_detail_text(pid)
    kb = build_provider_detail_keyboard(pid)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


# --- حذف کامل پروایدر ---

@admin_router.callback_query(F.data.startswith("pv_cdp_"))
async def cb_confirm_delete_provider(callback: CallbackQuery):
    pid = callback.data.replace("pv_cdp_", "").strip()
    prov = ProviderRegistry.get_provider(pid)
    name = prov.get("name", pid) if prov else pid

    confirm_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⚠️ بله، مطمئنم حذف شود", callback_data=f"pv_dp_{pid}"),
            ],
            [
                InlineKeyboardButton(text="❌ انصراف", callback_data=f"pv_o_{pid}"),
            ],
        ]
    )
    await callback.message.edit_text(
        f"⚠️ <b>آیا از حذف کامل پروایدر «{escape_html(name)}» اطمینان دارید؟</b>\n"
        "تمامی کلیدها و تنظیمات مربوط به این پروایدر پاک خواهد شد.",
        reply_markup=confirm_kb,
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data.startswith("pv_dp_"))
async def cb_do_delete_provider(callback: CallbackQuery):
    pid = callback.data.replace("pv_dp_", "").strip()
    ProviderRegistry.delete_provider(pid)
    await callback.answer(f"پروایدر {pid} با موفقیت حذف شد.", show_alert=True)
    await show_providers_list(callback)


# --- حذف مدل با کلید شیشه‌ای ---

@admin_router.callback_query(F.data.startswith("pv_dlm_"))
async def cb_menu_del_model(callback: CallbackQuery):
    pid = callback.data.replace("pv_dlm_", "").strip()
    prov = ProviderRegistry.get_provider(pid)
    if not prov or not prov.get("models"):
        await callback.answer("مدلی برای حذف وجود ندارد.", show_alert=True)
        return

    models = prov.get("models", [])
    kb_rows = []
    for idx, m in enumerate(models):
        kb_rows.append([
            InlineKeyboardButton(text=f"🗑 {m[:30]}", callback_data=f"pv_dm_{pid}_{idx}")
        ])
    kb_rows.append([InlineKeyboardButton(text="🔙 انصراف", callback_data=f"pv_o_{pid}")])

    await callback.message.edit_text(
        f"🗑 <b>انتخاب مدل جهت حذف از پروایدر {prov.get('name', pid)}:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data.startswith("pv_dm_"))
async def cb_do_del_model(callback: CallbackQuery):
    parts = callback.data.split("_")
    # pv, dm, pid, idx
    pid = parts[2]
    idx = int(parts[3])
    prov = ProviderRegistry.get_provider(pid)
    if prov and idx < len(prov.get("models", [])):
        m_name = prov["models"][idx]
        ProviderRegistry.remove_provider_model(pid, m_name)
        await callback.answer(f"مدل {m_name} حذف شد.")
    text = format_provider_detail_text(pid)
    kb = build_provider_detail_keyboard(pid)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


# --- حذف کلید با کلید شیشه‌ای ---

@admin_router.callback_query(F.data.startswith("pv_dlk_"))
async def cb_menu_del_key(callback: CallbackQuery):
    pid = callback.data.replace("pv_dlk_", "").strip()
    prov = ProviderRegistry.get_provider(pid)
    if not prov or not prov.get("api_keys"):
        await callback.answer("کلیدی برای حذف وجود ندارد.", show_alert=True)
        return

    keys = prov.get("api_keys", [])
    kb_rows = []
    for idx, k in enumerate(keys):
        k_name = k.get("name", f"کلید {idx+1}")
        kb_rows.append([
            InlineKeyboardButton(text=f"🗑 {k_name}", callback_data=f"pv_dk_{pid}_{idx}")
        ])
    kb_rows.append([InlineKeyboardButton(text="🔙 انصراف", callback_data=f"pv_o_{pid}")])

    await callback.message.edit_text(
        f"🗑 <b>انتخاب کلید جهت حذف از پروایدر {prov.get('name', pid)}:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data.startswith("pv_dk_"))
async def cb_do_del_key(callback: CallbackQuery):
    parts = callback.data.split("_")
    pid = parts[2]
    idx = int(parts[3])
    prov = ProviderRegistry.get_provider(pid)
    if prov and idx < len(prov.get("api_keys", [])):
        k_name = prov["api_keys"][idx]["name"]
        ProviderRegistry.remove_api_key(pid, k_name)
        await callback.answer(f"کلید {k_name} حذف شد.")
    text = format_provider_detail_text(pid)
    kb = build_provider_detail_keyboard(pid)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


# --- تعیین کلید پیش‌فرض با کلید شیشه‌ای ---

@admin_router.callback_query(F.data.startswith("pv_sdk_"))
async def cb_menu_set_def_key(callback: CallbackQuery):
    pid = callback.data.replace("pv_sdk_", "").strip()
    prov = ProviderRegistry.get_provider(pid)
    if not prov or not prov.get("api_keys"):
        await callback.answer("کلیدی برای انتخاب وجود ندارد.", show_alert=True)
        return

    keys = prov.get("api_keys", [])
    kb_rows = []
    for idx, k in enumerate(keys):
        k_name = k.get("name", f"کلید {idx+1}")
        star = " ⭐" if k.get("is_default") else ""
        kb_rows.append([
            InlineKeyboardButton(text=f"{k_name}{star}", callback_data=f"pv_sk_{pid}_{idx}")
        ])
    kb_rows.append([InlineKeyboardButton(text="🔙 انصراف", callback_data=f"pv_o_{pid}")])

    await callback.message.edit_text(
        f"⭐ <b>یک کلید را به عنوان کلید پیش‌فرض انتخاب کنید:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data.startswith("pv_sk_"))
async def cb_do_set_def_key(callback: CallbackQuery):
    parts = callback.data.split("_")
    pid = parts[2]
    idx = int(parts[3])
    prov = ProviderRegistry.get_provider(pid)
    if prov and idx < len(prov.get("api_keys", [])):
        k_name = prov["api_keys"][idx]["name"]
        ProviderRegistry.set_default_api_key(pid, k_name)
        await callback.answer(f"کلید {k_name} به عنوان پیش‌فرض تنظیم شد.", show_alert=True)
    text = format_provider_detail_text(pid)
    kb = build_provider_detail_keyboard(pid)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


# ==============================================================================
# ۵. فرآیند گام‌به‌گام افزودن پروایدر جدید (Interactive FSM)
# ==============================================================================

@admin_router.callback_query(F.data == "pv_start_add")
async def start_add_provider_fsm(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddProviderSG.waiting_for_id)
    text = (
        "📝 <b>افزودن پروایدر جدید (گام ۱ از ۴)</b>\n\n"
        "لطفاً یک <b>شناسه انگلیسی کوتاه و بدون فاصله</b> برای این پروایدر ارسال کنید:\n"
        "<i>(نمونه‌ها: <code>together</code>، <code>mistral</code>، <code>myapi</code>، <code>custom</code>)</i>"
    )
    await callback.message.edit_text(text, reply_markup=CANCEL_KB, parse_mode="HTML")


@admin_router.message(AddProviderSG.waiting_for_id)
async def fsm_provider_id_received(message: Message, state: FSMContext):
    raw_id = (message.text or "").strip().lower()
    clean_id = "".join(c for c in raw_id if c.isalnum() or c in ("-", "_"))
    if not clean_id or len(clean_id) < 2:
        await message.reply("❌ شناسه نامعتبر است. لطفاً حداقل ۲ کاراکتر انگلیسی وارد کنید:", reply_markup=CANCEL_KB)
        return

    await state.update_data(prov_id=clean_id)
    await state.set_state(AddProviderSG.waiting_for_name)
    await message.reply(
        f"✓ شناسه <code>{clean_id}</code> ثبت شد.\n\n"
        "🏷 <b>گام ۲ از ۴: نام نمایشی پروایدر</b>\n"
        "نامی که مایلید روی دکمه‌ها و منوها نمایش داده شود را ارسال کنید:\n"
        "<i>(مثال: <code>🚀 سرور اختصاصی من</code> یا <code>Together AI</code>)</i>",
        reply_markup=CANCEL_KB,
        parse_mode="HTML",
    )


@admin_router.message(AddProviderSG.waiting_for_name)
async def fsm_provider_name_received(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.reply("❌ نام نمی‌تواند خالی باشد. لطفاً نام را ارسال کنید:", reply_markup=CANCEL_KB)
        return

    await state.update_data(prov_name=name)
    await state.set_state(AddProviderSG.waiting_for_base_url)
    await message.reply(
        f"✓ نام <b>{escape_html(name)}</b> ثبت شد.\n\n"
        "🌐 <b>گام ۳ از ۴: آدرس Base URL</b>\n"
        "آدرس ریشه API سازگار با OpenAI را ارسال کنید:\n"
        "<i>(مثال: <code>https://api.together.xyz/v1</code> یا <code>http://localhost:1234/v1</code>)</i>",
        reply_markup=CANCEL_KB,
        parse_mode="HTML",
    )


@admin_router.message(AddProviderSG.waiting_for_base_url)
async def fsm_provider_url_received(message: Message, state: FSMContext):
    url = (message.text or "").strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        await message.reply("❌ آدرس باید با http:// یا https:// آغاز شود:", reply_markup=CANCEL_KB)
        return

    await state.update_data(prov_url=url)
    await state.set_state(AddProviderSG.waiting_for_initial_model)
    await message.reply(
        "✓ آدرس Base URL ثبت شد.\n\n"
        "🤖 <b>گام ۴ از ۴: نام مدل اولیه</b>\n"
        "نام یک مدل که قصد استفاده از آن در این پروایدر را دارید ارسال کنید:\n"
        "<i>(مثال: <code>meta-llama/Llama-3-70b-chat-hf</code> یا <code>gpt-4o-mini</code>)</i>",
        reply_markup=CANCEL_KB,
        parse_mode="HTML",
    )


@admin_router.message(AddProviderSG.waiting_for_initial_model)
async def fsm_provider_model_received(message: Message, state: FSMContext):
    model = (message.text or "").strip()
    if not model:
        await message.reply("❌ نام مدل نمی‌تواند خالی باشد:", reply_markup=CANCEL_KB)
        return

    data = await state.get_data()
    pid = data["prov_id"]
    pname = data["prov_name"]
    purl = data["prov_url"]

    # ثبت در رجیستری
    ProviderRegistry.add_provider(
        provider_id=pid,
        name=pname,
        base_url=purl,
        api_keys=[],
        models=[model],
    )
    await state.clear()

    await message.reply(
        f"🎉 <b>پروایدر جدید «{escape_html(pname)}» با موفقیت اضافه شد!</b>\n"
        f"اکنون می‌توانید با دکمه <b>🔑 افزودن کلید API</b>، کلیدهای اختصاصی آن را ثبت کنید.",
        parse_mode="HTML",
    )
    # نمایش آنی پنل پروایدر جدید
    text = format_provider_detail_text(pid)
    kb = build_provider_detail_keyboard(pid)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


# ==============================================================================
# ۶. فرآیند افزودن کلید نام‌دار (Add API Key with Name)
# ==============================================================================

@admin_router.callback_query(F.data.startswith("pv_adk_"))
async def start_add_key_fsm(callback: CallbackQuery, state: FSMContext):
    pid = callback.data.replace("pv_adk_", "").strip()
    prov = ProviderRegistry.get_provider(pid)
    pname = prov.get("name", pid) if prov else pid

    await state.set_state(AddKeySG.waiting_for_name)
    await state.update_data(target_pid=pid)

    await callback.message.edit_text(
        f"🔑 <b>افزودن کلید به پروایدر {escape_html(pname)} (گام ۱ از ۲)</b>\n\n"
        "لطفاً یک <b>عنوان یا نام دلخواه</b> برای این کلید بنویسید:\n"
        "<i>(مثال: <code>کلید اصلی</code>، <code>اکانت رایگان</code>، <code>Personal Token</code>)</i>",
        reply_markup=CANCEL_KB,
        parse_mode="HTML",
    )


@admin_router.message(AddKeySG.waiting_for_name)
async def fsm_key_name_received(message: Message, state: FSMContext):
    k_name = (message.text or "").strip()
    if not k_name:
        await message.reply("❌ عنوان کلید نمی‌تواند خالی باشد:", reply_markup=CANCEL_KB)
        return

    await state.update_data(key_name=k_name)
    await state.set_state(AddKeySG.waiting_for_key)
    await message.reply(
        f"✓ عنوان «{escape_html(k_name)}» ثبت شد.\n\n"
        "🔐 <b>گام ۲ از ۲: متن کلید API</b>\n"
        "لطفاً متن خود کلید API را ارسال کنید.\n\n"
        "🛡 <b>توجه امنیتی:</b> <i>پیام شما بلافاصله پس از ثبت جهت حفظ امنیت حذف خواهد شد و کلید به صورت ماسک‌شده نگهداری می‌شود.</i>",
        reply_markup=CANCEL_KB,
        parse_mode="HTML",
    )


@admin_router.message(AddKeySG.waiting_for_key)
async def fsm_key_val_received(message: Message, state: FSMContext):
    raw_key = (message.text or "").strip()
    # حذف فوری پیام حاوی کلید
    try:
        await message.delete()
    except Exception:
        pass

    if not raw_key or len(raw_key) < 5:
        await message.answer("❌ کلید معتبر نیست. حداقل ۵ کاراکتر نیاز است:", reply_markup=CANCEL_KB)
        return

    data = await state.get_data()
    pid = data["target_pid"]
    k_name = data["key_name"]

    ProviderRegistry.add_api_key(provider_id=pid, name=k_name, key=raw_key)
    await state.clear()

    await message.answer(
        f"✓ <b>کلید «{escape_html(k_name)}» با موفقیت ثبت شد!</b> (پیام ورودی جهت امنیت حذف گردید)",
        parse_mode="HTML",
    )
    # نمایش پنل پروایدر
    text = format_provider_detail_text(pid)
    kb = build_provider_detail_keyboard(pid)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


# ==============================================================================
# ۷. افزودن مدل جدید به پروایدر
# ==============================================================================

@admin_router.callback_query(F.data.startswith("pv_adm_"))
async def start_add_model_fsm(callback: CallbackQuery, state: FSMContext):
    pid = callback.data.replace("pv_adm_", "").strip()
    prov = ProviderRegistry.get_provider(pid)
    pname = prov.get("name", pid) if prov else pid

    await state.set_state(AddModelSG.waiting_for_model)
    await state.update_data(target_pid=pid)

    await callback.message.edit_text(
        f"🤖 <b>افزودن مدل جدید به پروایدر {escape_html(pname)}</b>\n\n"
        "لطفاً <b>نام دقیق مدل</b> را ارسال کنید:\n"
        "<i>(مثال: <code>gemini-2.5-flash</code> یا <code>qwen2.5:14b</code> یا <code>deepseek-v3</code>)</i>",
        reply_markup=CANCEL_KB,
        parse_mode="HTML",
    )


@admin_router.message(AddModelSG.waiting_for_model)
async def fsm_model_name_received(message: Message, state: FSMContext):
    m_name = (message.text or "").strip()
    if not m_name:
        await message.reply("❌ نام مدل نمی‌تواند خالی باشد:", reply_markup=CANCEL_KB)
        return

    data = await state.get_data()
    pid = data["target_pid"]

    ProviderRegistry.add_provider_model(pid, m_name)
    await state.clear()

    await message.reply(
        f"✓ مدل <code>{escape_html(m_name)}</code> با موفقیت به این پروایدر اضافه شد.",
        parse_mode="HTML",
    )
    text = format_provider_detail_text(pid)
    kb = build_provider_detail_keyboard(pid)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


# ==============================================================================
# ۸. تغییر نام و تغییر Base URL پروایدر
# ==============================================================================

@admin_router.callback_query(F.data.startswith("pv_en_"))
async def start_edit_name_fsm(callback: CallbackQuery, state: FSMContext):
    pid = callback.data.replace("pv_en_", "").strip()
    prov = ProviderRegistry.get_provider(pid)
    await state.set_state(EditProviderSG.waiting_for_name)
    await state.update_data(target_pid=pid)

    await callback.message.edit_text(
        f"✏️ <b>تغییر نام پروایدر</b>\nنام فعلی: <b>{prov.get('name', pid)}</b>\n\n"
        "لطفاً نام جدید را بنویسید:",
        reply_markup=CANCEL_KB,
        parse_mode="HTML",
    )


@admin_router.message(EditProviderSG.waiting_for_name)
async def fsm_edit_name_received(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.reply("❌ نام نمی‌تواند خالی باشد:", reply_markup=CANCEL_KB)
        return

    data = await state.get_data()
    pid = data["target_pid"]
    ProviderRegistry.rename_provider(pid, name)
    await state.clear()

    await message.reply(f"✓ نام به <b>{escape_html(name)}</b> تغییر یافت.", parse_mode="HTML")
    text = format_provider_detail_text(pid)
    kb = build_provider_detail_keyboard(pid)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


@admin_router.callback_query(F.data.startswith("pv_eu_"))
async def start_edit_url_fsm(callback: CallbackQuery, state: FSMContext):
    pid = callback.data.replace("pv_eu_", "").strip()
    prov = ProviderRegistry.get_provider(pid)
    await state.set_state(EditProviderSG.waiting_for_base_url)
    await state.update_data(target_pid=pid)

    await callback.message.edit_text(
        f"🔗 <b>تغییر آدرس Base URL</b>\nآدرس فعلی:\n<code>{prov.get('base_url', '')}</code>\n\n"
        "لطفاً آدرس جدید را ارسال کنید:",
        reply_markup=CANCEL_KB,
        parse_mode="HTML",
    )


@admin_router.message(EditProviderSG.waiting_for_base_url)
async def fsm_edit_url_received(message: Message, state: FSMContext):
    url = (message.text or "").strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        await message.reply("❌ آدرس باید با http:// یا https:// آغاز شود:", reply_markup=CANCEL_KB)
        return

    data = await state.get_data()
    pid = data["target_pid"]
    ProviderRegistry.set_provider_base_url(pid, url)
    await state.clear()

    await message.reply(f"✓ آدرس با موفقیت تغییر کرد.", parse_mode="HTML")
    text = format_provider_detail_text(pid)
    kb = build_provider_detail_keyboard(pid)
    await message.answer(text, reply_markup=kb, parse_mode="HTML")


# ==============================================================================
# ۹. انتخاب مدل برای اسلات استدلالی و اسلات سریع با دکمه شیشه‌ای (Slot Pickers)
# ==============================================================================

@admin_router.callback_query(F.data.in_({"pv_sl_r", "pv_sl_f"}))
async def cb_slot_pick_provider(callback: CallbackQuery):
    slot_type = "reasoning" if callback.data == "pv_sl_r" else "fast"
    slot_title = "استدلالی (Reasoning)" if slot_type == "reasoning" else "پرسرعت (Fast)"

    providers = ProviderRegistry.get_providers()
    kb_rows = []
    row = []
    for pid, pdata in providers.items():
        row.append(InlineKeyboardButton(text=pdata.get("name", pid), callback_data=f"pv_sp_{slot_type}_{pid}"))
        if len(row) == 2:
            kb_rows.append(row)
            row = []
    if row:
        kb_rows.append(row)
    kb_rows.append([InlineKeyboardButton(text="🔙 بازگشت به لیست پروایدرها", callback_data="cfg_provs")])

    await callback.message.edit_text(
        f"🎯 <b>انتخاب پروایدر برای اسلات {slot_title}:</b>\nپروایدر مورد نظر را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data.startswith("pv_sp_"))
async def cb_slot_pick_model(callback: CallbackQuery):
    parts = callback.data.split("_")
    # pv, sp, slot_type, pid
    slot_type = parts[2]
    pid = parts[3]
    prov = ProviderRegistry.get_provider(pid)
    if not prov or not prov.get("models"):
        await callback.answer("این پروایدر هیچ مدلی ندارد. ابتدا با افزودن مدل آن را کامل کنید.", show_alert=True)
        return

    models = prov.get("models", [])
    kb_rows = []
    for idx, m in enumerate(models):
        kb_rows.append([
            InlineKeyboardButton(text=f"🤖 {m[:32]}", callback_data=f"pv_sm_{slot_type}_{pid}_{idx}")
        ])
    kb_rows.append([
        InlineKeyboardButton(text="🔙 بازگشت به پروایدرها", callback_data=f"pv_sl_{'r' if slot_type == 'reasoning' else 'f'}")
    ])

    slot_title = "استدلالی" if slot_type == "reasoning" else "پرسرعت"
    await callback.message.edit_text(
        f"🤖 <b>انتخاب مدل برای اسلات {slot_title} از {prov.get('name', pid)}:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data.startswith("pv_sm_"))
async def cb_slot_set_model(callback: CallbackQuery):
    parts = callback.data.split("_")
    # pv, sm, slot_type, pid, idx
    slot_type = parts[2]
    pid = parts[3]
    idx = int(parts[4])
    prov = ProviderRegistry.get_provider(pid)
    if prov and idx < len(prov.get("models", [])):
        model_name = prov["models"][idx]
        if slot_type == "reasoning":
            ProviderRegistry.set_reasoning_slot(pid, model_name)
        else:
            ProviderRegistry.set_fast_slot(pid, model_name)
        await callback.answer(f"✓ اسلات {slot_type} روی {model_name} تنظیم شد.", show_alert=True)

    await show_providers_list(callback)


# ==============================================================================
# ۱۰. تنظیمات سایر بخش‌ها با کلید شیشه‌ای و FSM (Top-K, Error Channel, Prompt)
# ==============================================================================

@admin_router.callback_query(F.data == "cfg_topk")
async def cb_prompt_topk(callback: CallbackQuery, state: FSMContext):
    await state.set_state(TopKEditSG.waiting_for_k)
    await callback.message.edit_text(
        f"📚 <b>تنظیم سقف تردهای ارسالی (Top-K)</b>\n"
        f"تعداد فعلی: <b>{bot_settings.rag_top_k}</b> منبع\n\n"
        "لطفاً یک عدد بین <b>۱ تا ۲۰</b> ارسال کنید:",
        reply_markup=CANCEL_KB,
        parse_mode="HTML",
    )


@admin_router.message(TopKEditSG.waiting_for_k)
async def fsm_topk_received(message: Message, state: FSMContext):
    val = (message.text or "").strip()
    if not val.isdigit() or not (1 <= int(val) <= 25):
        await message.reply("❌ لطفاً یک عدد معتبر بین ۱ تا ۲۵ بفرستید:", reply_markup=CANCEL_KB)
        return
    k = int(val)
    set_setting("RAG_TOP_K", str(k))
    await state.clear()
    await message.reply(f"✓ سقف منابع ارسالی به <b>{k}</b> تغییر یافت.")
    await show_main_config(message)


@admin_router.callback_query(F.data == "cfg_errch")
async def cb_prompt_err_channel(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ErrorChannelSG.waiting_for_channel)
    curr = bot_settings.error_channel_id or "تعریف نشده"
    await callback.message.edit_text(
        f"🚨 <b>تنظیم کانال ثبت خطاها</b>\n"
        f"کانال فعلی: <code>{curr}</code>\n\n"
        "شناسه عددی کانال یا گروه لاگ را ارسال کنید (مثال: <code>-1001234567890</code>) یا آیدی چت خودتان:",
        reply_markup=CANCEL_KB,
        parse_mode="HTML",
    )


@admin_router.message(ErrorChannelSG.waiting_for_channel)
async def fsm_err_channel_received(message: Message, state: FSMContext):
    val = (message.text or "").strip()
    if not val:
        await message.reply("❌ شناسه نمی‌تواند خالی باشد:", reply_markup=CANCEL_KB)
        return
    set_setting("ERROR_CHANNEL_ID", val)
    await state.clear()
    await message.reply(f"✓ کانال خطاها روی <code>{escape_html(val)}</code> تنظیم شد.")
    await show_main_config(message)


@admin_router.callback_query(F.data == "cfg_prompt")
async def cb_prompt_menu(callback: CallbackQuery):
    is_custom = bot_settings.system_prompt != DEFAULT_SYSTEM_PROMPT
    prompt_preview = bot_settings.system_prompt[:350] + "..." if len(bot_settings.system_prompt) > 350 else bot_settings.system_prompt

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ ویرایش متن پرامپت", callback_data="cfg_do_edit_prompt")],
            [InlineKeyboardButton(text="📜 دریافت فایل پرامپت کامل", callback_data="cfg_send_full_prompt")],
            [InlineKeyboardButton(text="🛡️ بازنشانی به پرامپت پیش‌فرض مهندسی", callback_data="cfg_reset_prompt")],
            [InlineKeyboardButton(text="🔙 بازگشت به تنظیمات اصلی", callback_data="cfg_main")],
        ]
    )
    await callback.message.edit_text(
        f"📜 <b>تنظیمات پرامپت سیستم هوش مصنوعی</b>\n"
        f"وضعیت: <b>{'✏️ سفارشی‌شده' if is_custom else '🛡️ پیش‌فرض معماری سه‌گانه'}</b>\n\n"
        f"<i>پیش‌نمایش:</i>\n<pre>{escape_html(prompt_preview)}</pre>",
        reply_markup=kb,
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data == "cfg_do_edit_prompt")
async def cb_start_edit_prompt(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PromptEditSG.waiting_for_prompt)
    await callback.message.edit_text(
        "✏️ <b>ویرایش پرامپت سیستم</b>\n\n"
        "لطفاً متن کامل و جدید پرامپت را در قالب یک پیام ارسال کنید:\n"
        "(حداقل ۳۰ کاراکتر)",
        reply_markup=CANCEL_KB,
        parse_mode="HTML",
    )


@admin_router.message(PromptEditSG.waiting_for_prompt)
async def fsm_prompt_received(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if len(text) < 30:
        await message.reply("❌ متن بسیار کوتاه است. لطفاً پرامپت جامع ارسال کنید:", reply_markup=CANCEL_KB)
        return
    set_setting("SYSTEM_PROMPT", text)
    await state.clear()
    await message.reply(f"✓ پرامپت سیستم با موفقیت به‌روزرسانی شد ({len(text):,} کاراکتر).")
    await show_main_config(message)


@admin_router.callback_query(F.data == "cfg_reset_prompt")
async def cb_reset_prompt(callback: CallbackQuery):
    set_setting("SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT)
    await callback.answer("پرامپت به حالت پیش‌فرض معماری سه‌گانه بازگشت.", show_alert=True)
    await cb_prompt_menu(callback)


@admin_router.callback_query(F.data == "cfg_send_full_prompt")
async def cb_send_full_prompt(callback: CallbackQuery):
    prompt = bot_settings.system_prompt
    doc = BufferedInputFile(prompt.encode("utf-8"), filename="system_prompt.txt")
    await callback.message.answer_document(doc, caption=f"📜 پرامپت کامل سیستم ({len(prompt):,} کاراکتر)")
    await callback.answer()


# ==============================================================================
# ۱۱. آمار دیتابیس (/stats)
# ==============================================================================

@admin_router.message(Command("stats"))
async def cmd_stats(message: Message):
    with get_db() as conn:
        stats = get_stats(conn)
    report = f"""📊 <b>گزارش آمار پیام‌های گروه:</b>

• تعداد کل پیام‌ها: <b>{stats['total_messages']:,}</b>
• تعداد اعضای فرستنده: <b>{stats['total_senders']:,}</b>
• تعداد گفتگوهای ریپلای‌شده: <b>{stats['total_replies']:,}</b>
• بازه زمانی: از <b>{stats['first_message_date'][:10] if stats['first_message_date'] else '-'}</b> تا <b>{stats['last_message_date'][:10] if stats['last_message_date'] else '-'}</b>
"""
    await message.reply(report, parse_mode="HTML")
