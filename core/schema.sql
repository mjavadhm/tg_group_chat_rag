-- جدول جامع پیام‌های گروه تلگرام برای پشتیبانی کامل از متن، ریپلای و متادیتای انواع مدیا
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    
    -- مشخصات فرستنده
    sender_id INTEGER,
    sender_type TEXT,            -- 'user', 'channel', 'chat'
    sender_name TEXT,            -- نام و نام‌خانوادگی فرستنده
    sender_username TEXT,        -- یوزرنیم بدون @
    
    -- متن و محتوا
    text TEXT,                   -- متن اصلی پیام یا کپشن مدیا
    raw_text TEXT,               -- متن خام بدون فرمت‌بندی
    
    -- ردیابی سلسله‌مراتب و زنجیره ریپلای (پدر / فرزند)
    reply_to_msg_id INTEGER,     -- آیدی پیامی که به آن ریپلای زده شده
    reply_to_top_id INTEGER,     -- آیدی سرشاخه / تاپیک (در سوپرگروه‌های دارای تاپیک)
    is_topic_message INTEGER DEFAULT 0,
    
    -- زمان‌بندی
    date TEXT NOT NULL,          -- تاریخ و ساعت ارسال (UTC ISO-8601)
    edit_date TEXT,              -- تاریخ آخرین ویرایش (در صورت ادیت شدن)
    
    -- وضعیت فوروارد
    is_forward INTEGER DEFAULT 0,
    forward_from_id INTEGER,
    forward_from_name TEXT,
    forward_date TEXT,
    
    -- مشخصات رسانه‌ها (بدون دانلود فایل - صرفاً متادیتا)
    media_type TEXT,             -- 'photo', 'video', 'voice', 'audio', 'document', 'sticker', 'animation', 'poll', 'contact', 'location', 'web_page', NULL
    file_id TEXT,                -- شناسه یونیک سند یا عکس در تلگرام (document.id یا photo.id)
    file_unique_id TEXT,         -- شناسه اکسس هش تلگرام
    file_name TEXT,              -- نام فایل سندی
    mime_type TEXT,              -- نوع فایل (audio/ogg, image/jpeg, video/mp4, etc)
    file_size INTEGER,           -- حجم فایل به بایت
    duration INTEGER,            -- مدت زمان به ثانیه (برای ویس، صوت، ویدئو)
    width INTEGER,               -- عرض تصویر یا ویدئو
    height INTEGER,              -- ارتفاع تصویر یا ویدئو
    grouped_id INTEGER,          -- شناسه آلبوم تلگرام (برای فایل‌های ارسال شده به صورت چندتایی)
    
    -- آمار و تعاملات اعضا
    views INTEGER,               -- تعداد بازدید
    forwards INTEGER,            -- تعداد بازنشر
    replies_count INTEGER,       -- تعداد ریپلای‌هایی که این پیام دریافت کرده
    reactions_json TEXT,         -- ری‌اکشن‌ها به صورت ساختاریافته JSON
    
    -- ذخیره کامل ویژگی‌های خام به فرمت JSON برای نیازمندی‌های پیش‌بینی‌نشده آینده
    raw_json TEXT,
    
    created_at TEXT DEFAULT (datetime('now')),
    
    -- جلوگیری از ثبت تکراری پیام در یک چت
    UNIQUE(chat_id, message_id)
);

-- ایندکس‌ها برای سرعت جستجوی بالا در فازهای RAG، ادغام ریپلای‌ها و استخراج گفتگو
CREATE INDEX IF NOT EXISTS idx_messages_chat_msg ON messages(chat_id, message_id);
CREATE INDEX IF NOT EXISTS idx_messages_reply_to ON messages(chat_id, reply_to_msg_id);
CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_id);
CREATE INDEX IF NOT EXISTS idx_messages_date ON messages(date);
CREATE INDEX IF NOT EXISTS idx_messages_media ON messages(media_type);
CREATE INDEX IF NOT EXISTS idx_messages_grouped ON messages(grouped_id);

-- جدول نگهداری وضعیت و پیشرفت کراولر (برای امکان Stop و Resume خودکار بدون از دست رفتن رکوردها)
CREATE TABLE IF NOT EXISTS crawl_state (
    chat_id INTEGER PRIMARY KEY,
    chat_title TEXT,
    chat_username TEXT,
    oldest_msg_id INTEGER,
    newest_msg_id INTEGER,
    total_crawled INTEGER DEFAULT 0,
    last_run_at TEXT,
    status TEXT DEFAULT 'idle'   -- 'running', 'completed', 'idle'
);
