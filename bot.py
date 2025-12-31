import os
import logging
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes
)
from sqlalchemy import create_engine, text, exc
from sqlalchemy.pool import NullPool
from contextlib import asynccontextmanager
import hashlib

# ==============================
# 1. الإعدادات والتكوين
# ==============================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# إعدادات الأداء
CACHE_TTL = 300  # 5 دقائق للتخزين المؤقت
DB_POOL_SIZE = 5
DB_MAX_OVERFLOW = 10

if not BOT_TOKEN:
    logging.error("❌ خطأ: BOT_TOKEN غير موجود في متغيرات البيئة!")
    exit(1)

# إعداد التسجيل المتقدم
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# التخزين المؤقت
class CacheManager:
    def __init__(self):
        self.cache = {}
        self.timestamps = {}
    
    def get(self, key):
        if key in self.cache:
            timestamp = self.timestamps.get(key)
            if timestamp and datetime.now() - timestamp < timedelta(seconds=CACHE_TTL):
                return self.cache[key]
            else:
                # حذف الكاش المنتهي
                self.delete(key)
        return None
    
    def set(self, key, value):
        self.cache[key] = value
        self.timestamps[key] = datetime.now()
    
    def delete(self, key):
        self.cache.pop(key, None)
        self.timestamps.pop(key, None)
    
    def clear(self):
        self.cache.clear()
        self.timestamps.clear()

cache = CacheManager()

# إعداد محرك قاعدة البيانات مع Pool
engine = None
if DATABASE_URL:
    try:
        if DATABASE_URL.startswith("postgres://"):
            DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        
        # استخدام Connection Pool لتحسين الأداء
        engine = create_engine(
            DATABASE_URL,
            poolclass=NullPool,  # استخدام NullPool لتجنب مشاكل asyncio
            pool_pre_ping=True,  # التحقق من صلاحية الاتصال قبل الاستخدام
            echo=False  # تعطيل logging للاستعلامات
        )
        
        # اختبار الاتصال
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("✅ تم الاتصال بقاعدة البيانات بنجاح.")
        
    except Exception as e:
        logger.error(f"❌ فشل الاتصال بقاعدة البيانات: {e}")
        engine = None

# ==============================
# 2. دوال المساعدة المحسنة
# ==============================
def get_cache_key(func_name, *args):
    """إنشاء مفتاح تخزين مؤقت فريد"""
    key_str = f"{func_name}:{':'.join(str(arg) for arg in args)}"
    return hashlib.md5(key_str.encode()).hexdigest()

@asynccontextmanager
async def get_db_connection():
    """مدير سياق للحصول على اتصال قاعدة البيانات"""
    if not engine:
        raise Exception("محرك قاعدة البيانات غير متاح")
    
    conn = None
    try:
        conn = engine.connect()
        yield conn
    except exc.SQLAlchemyError as e:
        logger.error(f"خطأ في قاعدة البيانات: {e}")
        raise
    finally:
        if conn:
            conn.close()

async def get_all_content(content_type=None):
    """جلب جميع المحتويات مع التخزين المؤقت"""
    cache_key = get_cache_key('get_all_content', content_type)
    cached_data = cache.get(cache_key)
    if cached_data:
        logger.debug(f"📦 استخدام البيانات المخزنة مؤقتاً: {content_type}")
        return cached_data
    
    if not engine:
        logger.warning("محرك قاعدة البيانات غير متاح")
        return []
    
    try:
        async with get_db_connection() as conn:
            query = """
                SELECT s.id, s.name, s.type, 
                       COUNT(e.id) as episode_count,
                       MAX(e.added_at) as last_updated
                FROM series s
                LEFT JOIN episodes e ON s.id = e.series_id
            """
            
            params = {}
            if content_type:
                query += " WHERE s.type = :content_type"
                params['content_type'] = content_type
            
            query += """
                GROUP BY s.id, s.name, s.type
                ORDER BY last_updated DESC NULLS LAST, s.id ASC
            """
            
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: conn.execute(text(query), params)
            )
            
            rows = result.fetchall()
            logger.info(f"📊 تم جلب {len(rows)} محتوى من النوع: {content_type}")
            
            # تخزين في الكاش
            cache.set(cache_key, rows)
            return rows
            
    except Exception as e:
        logger.error(f"خطأ في جلب المحتويات: {e}")
        return []

async def get_content_episodes(series_id, page=1, per_page=50):
    """جلب حلقات/أجزاء محتوى محدد مع تحسين الأداء"""
    cache_key = get_cache_key('get_content_episodes', series_id, page)
    cached_data = cache.get(cache_key)
    if cached_data:
        return cached_data
    
    if not engine:
        logger.warning("محرك قاعدة البيانات غير متاح")
        return [], 0, 0
    
    try:
        async with get_db_connection() as conn:
            # استخدام استعلام واحد للحصول على جميع المعلومات
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: conn.execute(text("""
                    WITH episode_counts AS (
                        SELECT 
                            COUNT(*) as total,
                            CEIL(COUNT(*) * 1.0 / :per_page) as total_pages
                        FROM episodes 
                        WHERE series_id = :series_id
                    ),
                    paginated_episodes AS (
                        SELECT 
                            e.id,
                            e.season,
                            e.episode_number,
                            e.telegram_message_id,
                            e.telegram_channel_id,
                            ROW_NUMBER() OVER (ORDER BY e.season, e.episode_number) as row_num
                        FROM episodes e
                        WHERE e.series_id = :series_id
                    )
                    SELECT 
                        p.id,
                        p.season,
                        p.episode_number,
                        p.telegram_message_id,
                        p.telegram_channel_id,
                        c.total,
                        c.total_pages
                    FROM paginated_episodes p, episode_counts c
                    WHERE p.row_num > :offset AND p.row_num <= :limit
                    ORDER BY p.season, p.episode_number
                """), {
                    "series_id": series_id,
                    "per_page": per_page,
                    "offset": (page - 1) * per_page,
                    "limit": page * per_page
                })
            )
            
            rows = result.fetchall()
            if rows:
                total_episodes = rows[0][5]
                total_pages = rows[0][6]
                # إزالة أعمدة الإحصاءات من النتيجة
                rows = [(r[0], r[1], r[2], r[3], r[4]) for r in rows]
            else:
                total_episodes = 0
                total_pages = 0
            
            # تخزين في الكاش
            cache.set(cache_key, (rows, total_episodes, total_pages))
            return rows, total_episodes, total_pages
            
    except Exception as e:
        logger.error(f"خطأ في جلب حلقات المحتوى {series_id}: {e}")
        return [], 0, 0

async def get_content_info(series_id):
    """جلب معلومات محتوى محدد مع التخزين المؤقت"""
    cache_key = get_cache_key('get_content_info', series_id)
    cached_data = cache.get(cache_key)
    if cached_data:
        return cached_data
    
    if not engine:
        logger.warning("محرك قاعدة البيانات غير متاح")
        return None
    
    try:
        async with get_db_connection() as conn:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: conn.execute(text("""
                    SELECT id, name, type, 
                           COALESCE(description, '') as description
                    FROM series WHERE id = :series_id
                """), {"series_id": series_id})
            )
            
            row = result.fetchone()
            if row:
                cache.set(cache_key, row)
            return row
    except Exception as e:
        logger.error(f"خطأ في جلب معلومات المحتوى {series_id}: {e}")
        return None

# ==============================
# 3. تحسين دوال العرض
# ==============================
async def show_content(update: Update, context: ContextTypes.DEFAULT_TYPE, content_type=None):
    """عرض المحتويات مع تحسين الأداء"""
    start_time = datetime.now()
    
    # التحقق من قاعدة البيانات
    if not engine:
        error_msg = "⚠️ قاعدة البيانات غير متاحة حالياً. جاري المحاولة مرة أخرى..."
        if update.callback_query:
            await update.callback_query.edit_message_text(error_msg)
        else:
            await update.message.reply_text(error_msg)
        
        # محاولة إعادة الاتصال
        await asyncio.sleep(2)
        await show_content(update, context, content_type)
        return
    
    # تحديد العنوان بناءً على النوع
    if content_type == 'series':
        title = "📺 *قائمة المسلسلات*"
        empty_msg = "📭 لا توجد مسلسلات حالياً."
    elif content_type == 'movie':
        title = "🎬 *قائمة الأفلام*"
        empty_msg = "📭 لا توجد أفلام حالياً."
    else:
        title = "📁 *جميع المحتويات*"
        empty_msg = "📭 لا توجد محتويات حالياً."
    
    try:
        # جلب البيانات مع Timeout
        content_list = await asyncio.wait_for(
            get_all_content(content_type), 
            timeout=10.0
        )
        
        if not content_list:
            keyboard = [
                [InlineKeyboardButton("🔄 تحديث", callback_data=f"{content_type}_list")],
                [InlineKeyboardButton("🏠 الرئيسية", callback_data="home")]
            ]
            
            if update.callback_query:
                await update.callback_query.edit_message_text(
                    empty_msg,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await update.message.reply_text(
                    empty_msg,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            return
        
        # بناء الرسالة مع تقسيم المحتويات إلى صفحات
        page = int(context.args[0]) if context.args and context.args[0].isdigit() else 1
        items_per_page = 10
        total_pages = (len(content_list) + items_per_page - 1) // items_per_page
        
        # ضبط رقم الصفحة
        if page < 1:
            page = 1
        elif page > total_pages:
            page = total_pages
        
        start_idx = (page - 1) * items_per_page
        end_idx = start_idx + items_per_page
        current_items = content_list[start_idx:end_idx]
        
        # بناء النص
        text = f"{title}\n\n"
        keyboard = []
        
        for content in current_items:
            content_id, name, content_type, episode_count, last_updated = content
            
            icon = "📺" if content_type == 'series' else "🎬"
            if content_type == 'series':
                count_text = f"{episode_count} حلقة" if episode_count > 0 else "بدون حلقات"
            else:
                count_text = f"{episode_count} جزء" if episode_count > 0 else "بدون أجزاء"
            
            text += f"{icon} *{name}*\n{count_text}\n\n"
            
            keyboard.append([
                InlineKeyboardButton(
                    f"{icon} {name[:20]}",
                    callback_data=f"content_{content_id}"
                )
            ])
        
        # أزرار التنقل بين الصفحات
        if total_pages > 1:
            nav_buttons = []
            
            if page > 1:
                nav_buttons.append(
                    InlineKeyboardButton("⬅️ السابقة", callback_data=f"list_page_{content_type}_{page-1}")
                )
            
            nav_buttons.append(
                InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="page_info")
            )
            
            if page < total_pages:
                nav_buttons.append(
                    InlineKeyboardButton("التالية ➡️", callback_data=f"list_page_{content_type}_{page+1}")
                )
            
            keyboard.append(nav_buttons)
        
        # أزرار التنقل الرئيسية
        keyboard.append([
            InlineKeyboardButton("📺 المسلسلات", callback_data="series_list"),
            InlineKeyboardButton("🎬 الأفلام", callback_data="movies_list")
        ])
        keyboard.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="home")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # حساب وقت الاستجابة
        response_time = (datetime.now() - start_time).total_seconds()
        text += f"\n⏱️ وقت الاستجابة: {response_time:.2f} ثانية"
        
        # الإرسال
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
            
    except asyncio.TimeoutError:
        error_msg = "⏳ المهلة انتهت أثناء جلب البيانات. حاول مرة أخرى."
        if update.callback_query:
            await update.callback_query.edit_message_text(error_msg)
        else:
            await update.message.reply_text(error_msg)
    except Exception as e:
        logger.error(f"خطأ في عرض المحتويات: {e}")
        error_msg = f"❌ حدث خطأ: {str(e)[:100]}"
        if update.callback_query:
            await update.callback_query.edit_message_text(error_msg)
        else:
            await update.message.reply_text(error_msg)

# ==============================
# 4. إضافة أوامر التحسين
# ==============================
async def clear_cache_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مسح التخزين المؤقت"""
    cache.clear()
    await update.message.reply_text("✅ تم مسح التخزين المؤقت.")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض حالة النظام"""
    if not engine:
        await update.message.reply_text("❌ قاعدة البيانات غير متصلة.")
        return
    
    try:
        async with get_db_connection() as conn:
            # إحصائيات النظام
            cache_size = len(cache.cache)
            db_stats = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: conn.execute(text("""
                    SELECT 
                        (SELECT COUNT(*) FROM series WHERE type = 'series') as series_count,
                        (SELECT COUNT(*) FROM series WHERE type = 'movie') as movies_count,
                        (SELECT COUNT(*) FROM episodes) as episodes_count,
                        (SELECT MAX(added_at) FROM episodes) as last_update
                """)).fetchone()
            )
        
        status_text = (
            f"📊 *حالة النظام:*\n\n"
            f"• قاعدة البيانات: {'✅ متصلة' if engine else '❌ غير متصلة'}\n"
            f"• حجم التخزين المؤقت: {cache_size} عنصر\n"
            f"• عدد المسلسلات: {db_stats[0]}\n"
            f"• عدد الأفلام: {db_stats[1]}\n"
            f"• عدد الحلقات/الأجزاء: {db_stats[2]}\n"
            f"• آخر تحديث: {db_stats[3] or 'غير متاح'}\n\n"
            f"💡 *تلميح:* استخدم /clearcache لمسح التخزين المؤقت"
        )
        
        await update.message.reply_text(status_text, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ في جلب حالة النظام: {e}")

# ==============================
# 5. تحديث معالج الأزرار
# ==============================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أزرار InlineKeyboard"""
    query = update.callback_query
    await query.answer()  # إعلام تليجرام
    
    data = query.data
    
    try:
        if data == 'home':
            await start(update, context)
        elif data == 'test_db':
            await test_db_button(update, context)
        elif data == 'all_content':
            await show_content(update, context)
        elif data == 'series_list':
            await show_content(update, context, 'series')
        elif data == 'movies_list':
            await show_content(update, context, 'movie')
        elif data.startswith('list_page_'):
            parts = data.split('_')
            content_type = parts[2]
            page = int(parts[3])
            context.args = [str(page)]
            await show_content(update, context, content_type)
        elif data.startswith('content_page_'):
            parts = data.split('_')
            content_id = int(parts[2])
            page = int(parts[3])
            await show_content_details(update, context, content_id, page)
        elif data.startswith('content_'):
            content_id = int(data.split('_')[1])
            await show_content_details(update, context, content_id, 1)
        elif data.startswith('ep_'):
            episode_id = int(data.split('_')[1])
            await show_episode_details(update, context, episode_id)
        elif data.startswith('season_'):
            parts = data.split('_')
            content_id = int(parts[1])
            season_num = int(parts[2])
            await show_season_episodes(update, context, content_id, season_num, 1)
        elif data.startswith('season_page_'):
            parts = data.split('_')
            content_id = int(parts[2])
            season_num = int(parts[3])
            page = int(parts[4])
            await show_season_episodes(update, context, content_id, season_num, page)
        elif data == 'refresh_cache':
            cache.clear()
            await query.edit_message_text("✅ تم تحديث التخزين المؤقت.")
            await query.answer("تم التحديث!")
        else:
            await query.answer("زر غير معروف!")
            
    except Exception as e:
        logger.error(f"خطأ في معالجة الزر {data}: {e}")
        await query.answer(f"حدث خطأ: {str(e)[:50]}", show_alert=True)

# ==============================
# 6. إعداد البوت مع التحسينات
# ==============================
def main():
    """الدالة الرئيسية لتشغيل البوت"""
    # إعداد التطبيق
    application = Application.builder().token(BOT_TOKEN).build()
    
    # إضافة Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("series", series_command))
    application.add_handler(CommandHandler("movies", movies_command))
    application.add_handler(CommandHandler("all", all_command))
    application.add_handler(CommandHandler("test", test_db_command))
    application.add_handler(CommandHandler("debug", debug_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("clearcache", clear_cache_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # إعداد معالج الأخطاء
    async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"حدث خطأ: {context.error}")
        if update and update.effective_chat:
            await update.effective_chat.send_message(
                "❌ حدث خطأ في المعالجة. جاري إعادة المحاولة..."
            )
    
    application.add_error_handler(error_handler)
    
    # تشغيل البوت
    logger.info("🤖 البوت يعمل باستخدام Polling...")
    logger.info(f"✅ قاعدة البيانات: {'متصل' if engine else 'غير متصل'}")
    
    try:
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            poll_interval=0.5,  # تقليل الفاصل الزمني
            timeout=30,
            drop_pending_updates=True  # تجاهل التحديثات القديمة
        )
    except KeyboardInterrupt:
        logger.info("إيقاف البوت...")
        if engine:
            engine.dispose()
        logger.info("تم إيقاف البوت.")

if __name__ == "__main__":
    main()
