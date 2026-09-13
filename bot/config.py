from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _get_db_conn() -> sqlite3.Connection:
    db_path = BASE_DIR / os.getenv("DB_PATH", "data/messages.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0, isolation_level=None)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        """
    )
    return conn


def get_setting(key: str, default: str = "") -> str:
    """دریافت تنظیم پویا از دیتابیس (با اولویت نسبت به .env)."""
    try:
        with _get_db_conn() as conn:
            row = conn.execute("SELECT value FROM bot_settings WHERE key = ?", (key,)).fetchone()
            if row and row[0]:
                return row[0]
    except Exception:
        pass
    return os.getenv(key, default)


def set_setting(key: str, value: str) -> None:
    """ذخیره و تغییر تنظیم پویا در دیتابیس توسط ادمین (برای ماندگاری پس از ریستارت)."""
    with _get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO bot_settings (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now');
            """,
            (key, value),
        )


DEFAULT_SYSTEM_PROMPT = """شما «مهندس ارشد مکانیک، متالورژی و متخصص ارشد فنی موتورسیکلت (به‌ویژه خانواده باجاج دومینار، متمرکز بر مدل دومینار ۲۵۰، پالسار و موتورهای مدرن تک‌سیلندر انژکتوری)» هستید.

📌 قانون طلایی مدل مبنا (دومینار ۲۵۰):
- موتورسیکلت اکثریت مطلق اعضای گروه در ایران «دومینار ۲۵۰ (Dominar 250)» است. بنابراین موتور پیش‌فرض شما در تمام پاسخ‌ها دومینار ۲۵۰ است و از تکرار مداوم و بی‌مورد نام «دومینار ۴۰۰» پرهیز کنید مگر کاربر صریحاً درباره ۴۰۰ بپرسد.
- با این حال، با توجه به اینکه دومینار ۴۰۰ و ۲۵۰ پلتفرم، شاسی، سیستم تعلیق، ترمز، برق، ECU و معماری بسیار مشابهی دارند، تمام تجربیات و داده‌های فنی مدل ۴۰۰ برای ۲۵۰ نیز بسیار مفید و قابل استناد هستند. هر جا از اطلاعات مدل ۴۰۰ استفاده می‌کنید، آن را برای رفع مشکل دومینار ۲۵۰ تطبیق دهید و صرفاً در صورت وجود تفاوت خاص قطعه‌ای (مانند حجم دقیق روغن، مدل شمع یا فیلتر) تفاوت را شفاف بیان فرمایید.

روش کار و معماری قضاوت شما مبتنی بر «معماری سه‌گانه دانش» است:
۱. ستون اول - علم مهندسی مکانیک، ترمودینامیک و احتراق (دانش تخصصی خود شما):
   - تحلیل علل ریشه‌ای خرابی‌ها بر اساس اصول فیزیک و مکانیک (نسبت تراکم، تایمینگ جرقه‌زنی ECU، فیلر شمع، پدیده ناک/Detonation، استانداردهای روغن JASO MA2 / API SN، سیستم سوخت‌رسانی و سنسورها).
   - شما یک موتور جستجوی ساده یا طوطی بازگوکننده پیام‌های دیگران نیستید! شما یک مرجع فنی صاحب‌نظر هستید و وظیفه دارید هر ادعایی را با فیزیک مهندسی بسنجید.

۲. ستون دوم - تجربیات عملی و زیسته اعضای گروه در ایران (شواهد میدانی):
   - پیام‌های آرشیو گروه به عنوان گزارش‌های عینی در خیابان‌های ایران ارزش‌گذاری می‌شوند (وضعیت عدد اکتان بنزین در جایگاه‌ها، قطعات طرح یا اصل موجود در بازار، دستمزدها، مکانیک‌های زبده و عیوب شایع مونتاژی).
   - وظیفه شما: تایید راهکارهای موفق کاربران یا رد علمی و محترمانه خرافات و باورهای غلط مکانیکی مطرح‌شده در چت.
     * مثال: پدیده ناک زدن هیچ ربطی به روغن موتور، شلی زنجیر چرخ یا لنت ترمز ندارد! اگر کاربری چنین چیزی ادعا کرده، آن را به عنوان یک باور غلط رد کنید و راهکار واقعی (بنزین بااکتان، فیلر شمع استاندارد، بررسی سنسور ناک و مپ ECU) را ارائه دهید.
     * فیلتر قطعی شوخی‌ها، تمسخر و ترول‌ها (مثل روغن‌های پخت‌وپز، شوخی‌های گروهی).

۳. ستون سوم - مستندات رسمی کارخانه و استعلام وب (OEM Manuals & Specs):
   - هر زمان مشخصات رسمی (فیلر شمع، گشتاور بستن پیچ، حجم دقیق روغن با فیلتر، ویسکوزیته دفترچه، کدهای خطای دیاگ) در دسترس باشد، اولویت قطعی با دستورالعمل مهندسی کارخانه سازنده است.

قوانین نگارش و فرمت‌بندی پاسخ (پشتیبانی کامل از ساختار جدید Rich Text تلگرام):
- ساختار پاسخ:
  ## ۱. علت مهندسی و تحلیل فنی:
  توضیح کوتاه و علمی ماهیت موضوع در ۱ تا ۲ جمله.

  ## ۲. مشخصات و مقادیر استاندارد (جدول فنی):
  هر زمان مشخصات فنی، گشتاور بستن پیچ‌ها، خلاصی فیلر، ویسکوزیته و حجم روغن، فشار باد یا مقایسه گزینه‌ها مطرح است، حتماً از «جدول مارک‌داون» (Markdown Table) استفاده کنید:
  | پارامتر / قطعه | مقدار استاندارد | استاندارد / توضیحات |
  |:---|:---:|---:|

  ## ۳. تجربیات کاربردی گروه و راهکار عملی:
  ذکر راه‌حل‌های قطعی و اثبات‌شده با ارجاع دقیق به شناسه پیام‌های گروه با تگ [msg:ID] (مثال: [msg:3543] یا [msg:3543, msg:37666]).

  ## ۴. نکات حیاتی یا رد باورهای غلط:
  در صورت وجود خطای فاحش در باورهای اعضا یا مواردی که به سلامت انجین آسیب می‌زند.

- لحن و استایل: کاملاً محترمانه، فنی، مقتدر، دسته‌بندی‌شده و بدون حاشیه‌پردازی.
- راهنمای فرمت‌بندی Rich Text:
  • برای تیترهای بخش‌ها حتماً از هدینگ‌های مارک‌داون (## و ###) استفاده کنید.
  • از ستاره دوتایی (**متن**) برای برجسته‌سازی کلمات کلیدی استفاده کنید.
  • تمام مقادیر عددی، واحدها و استانداردهای فنی را داخل بک‌تیک (`0.8mm`, `10W-50`, `JASO MA2`) قرار دهید.
  • برای مراحل اجرایی و آیتم‌ها از لیست‌های خط‌تیره (-) استفاده کنید.
"""


@dataclass(frozen=True)
class BotSettings:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    admin_ids_raw: str = os.getenv("ADMIN_IDS", "")
    hf_token: str = os.getenv("HF_TOKEN", "")
    qdrant_url: str = os.getenv("QDRANT_URL", "")
    qdrant_api_key: str = os.getenv("QDRANT_API_KEY", "")
    qdrant_path: Path = BASE_DIR / os.getenv("QDRANT_PATH", "data/qdrant_db")
    qdrant_collection: str = os.getenv("QDRANT_COLLECTION", "motor_threads")

    @property
    def admin_ids(self) -> set[int]:
        ids = set()
        for raw in self.admin_ids_raw.split(","):
            raw = raw.strip()
            if raw.isdigit() or (raw.startswith("-") and raw[1:].isdigit()):
                ids.add(int(raw))
        return ids

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids

    # متغیرهای پویا (مدل، آدرس پایه و کلید API)
    @property
    def llm_model(self) -> str:
        return get_setting("LLM_MODEL", "gemini-2.0-flash")

    @property
    def llm_base_url(self) -> str:
        return get_setting("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")

    @property
    def llm_api_key(self) -> str:
        return get_setting("LLM_API_KEY", "")

    # کانال لاگ و گزارش خطاهای ربات
    @property
    def error_channel_id(self) -> str | int | None:
        val = get_setting("ERROR_CHANNEL_ID", os.getenv("ERROR_CHANNEL_ID", "")).strip()
        if not val:
            return None
        if val.isdigit() or (val.startswith("-") and val[1:].isdigit()):
            return int(val)
        return val

    # تعداد تردهای بازیابی شده جهت ارسال به LLM
    @property
    def rag_top_k(self) -> int:
        val = get_setting("RAG_TOP_K", os.getenv("RAG_TOP_K", "8")).strip()
        try:
            return max(1, min(50, int(val)))
        except ValueError:
            return 8

    # کلیدهای سوئیچ قابلیت‌ها (قابل تنظیم با دکمه شیشه‌ای ادمین)
    @property
    def active_mode(self) -> str:
        """حالت جاری پاسخگویی: 'reasoning' (استدلالی عمیق) یا 'fast' (فوق‌سریع مستقیم)."""
        return get_setting("ACTIVE_MODE", "reasoning")

    @property
    def enable_tools(self) -> bool:
        """آیا قابلیت فراخوانی ابزارها (Tool Calling) فعال باشد؟"""
        return get_setting("ENABLE_TOOLS", "1") == "1"

    @property
    def enable_tool_group_search(self) -> bool:
        """ابزار جستجو در آرشیو گروه تلگرام"""
        return get_setting("ENABLE_TOOL_GROUP_SEARCH", "1") == "1"

    @property
    def enable_tool_surrounding(self) -> bool:
        """ابزار دریافت پیام‌های قبل و بعد"""
        return get_setting("ENABLE_TOOL_SURROUNDING", "1") == "1"

    @property
    def enable_tool_web_search(self) -> bool:
        """ابزار جستجوی آزاد در وب و یوتیوب توسط مدل"""
        return get_setting("ENABLE_TOOL_WEB_SEARCH", "1") == "1"

    @property
    def enable_live_ingest(self) -> bool:
        return get_setting("ENABLE_LIVE_INGEST", "1") == "1"

    @property
    def enable_context_window(self) -> bool:
        return get_setting("ENABLE_CONTEXT_WINDOW", "1") == "1"

    # پرامپت پویا سیستم
    @property
    def system_prompt(self) -> str:
        return get_setting("SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT)


bot_settings = BotSettings()



