import os
import logging
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes
)
from sqlalchemy import create_engine, text

# ==============================
# 1. الإعدادات والتكوين
# ==============================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

if not BOT_TOKEN:
    print("❌ خطأ: BOT_TOKEN غير موجود في متغيرات البيئة!")
    exit(1)

if not DATABASE_URL:
    print("⚠️ تحذير: DATABASE_URL غير موجود. قد لا تعرض المحتويات.")

# إصلاح رابط قاعدة البيانات
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# إعداد التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# التخزين المؤقت البسيط
class SimpleCache:
    def __init__(self):
        self.cache = {}
        self.timestamps = {}
    
    def get(self, key, ttl=300):
        """الحصول على قيمة من التخزين المؤقت"""
        if key in self.cache:
            if key in self.timestamps:
                if datetime.now() - self.timestamps[key] < timedelta(seconds=ttl):
                    return self.cache[key]
            del self.cache[key]
            del self.timestamps[key]
        return None
    
    def set(self, key, value):
        """تعيين قيمة في التخزين المؤقت"""
        self.cache[key] = value
        self.timestamps[key] = datetime.now()
    
    def clear(self):
        """مسح التخزين المؤقت"""
        self.cache.clear()
        self.timestamps.clear()

cache = SimpleCache()

# محرك قاعدة البيانات
engine = None
if DATABASE_URL:
    try:
        engine = create_engine(DATABASE_URL)
        # اختبار الاتصال
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("✅ تم الاتصال بقاعدة البيانات بنجاح.")
        
    except Exception as e:
        logger.error(f"❌ فشل الاتصال بقاعدة البيانات: {e}")
        engine = None

# ==============================
# 2. دوال المساعدة
# ==============================
async def get_all_content(content_type=None):
    """جلب جميع المحتويات من قاعدة البيانات حسب النوع"""
    cache_key = f"all_content_{content_type}"
    cached = cache.get(cache_key)
    if cached:
        logger.debug(f"📦 استخدام البيانات المخزنة مؤقتاً: {content_type}")
        return cached
    
    if not engine:
        logger.warning("محرك قاعدة البيانات غير متاح")
        return []
    
    try:
        with engine.connect() as conn:
            query = """
                SELECT s.id, s.name, s.type, COUNT(e.id) as episode_count
                FROM series s
                LEFT JOIN episodes e ON s.id = e.series_id
            """
            
            if content_type:
                query += f" WHERE s.type = '{content_type}'"
            
            query += """
                GROUP BY s.id, s.name, s.type
                ORDER BY s.id ASC
            """
            
            result = conn.execute(text(query))
            rows = result.fetchall()
            
            logger.info(f"📊 تم جلب {len(rows)} محتوى من النوع: {content_type}")
            cache.set(cache_key, rows)
            return rows
            
    except Exception as e:
        logger.error(f"❌ خطأ في جلب المحتويات: {e}")
        return []

async def get_content_episodes(series_id, page=1, per_page=50):
    """جلب حلقات/أجزاء محتوى محدد"""
    cache_key = f"content_episodes_{series_id}_{page}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    if not engine:
        logger.warning("محرك قاعدة البيانات غير متاح")
        return [], 0, 0
    
    try:
        with engine.connect() as conn:
            # حساب العدد الإجمالي
            count_result = conn.execute(text("""
                SELECT COUNT(*) FROM episodes WHERE series_id = :series_id
            """), {"series_id": series_id})
            total_episodes = count_result.scalar()
            
            # حساب عدد الصفحات
            total_pages = (total_episodes + per_page - 1) // per_page
            
            # ضبط رقم الصفحة
            if page < 1:
                page = 1
            elif page > total_pages and total_pages > 0:
                page = total_pages
            
            offset = (page - 1) * per_page
            
            # جلب الحلقات
            result = conn.execute(text("""
                SELECT e.id, e.season, e.episode_number, 
                       e.telegram_message_id, e.telegram_channel_id
                FROM episodes e
                WHERE e.series_id = :series_id
                ORDER BY e.season, e.episode_number
                LIMIT :limit OFFSET :offset
            """), {
                "series_id": series_id,
                "limit": per_page,
                "offset": offset
            })
            
            rows = result.fetchall()
            result_data = (rows, total_episodes, total_pages)
            cache.set(cache_key, result_data)
            return result_data
            
    except Exception as e:
        logger.error(f"❌ خطأ في جلب حلقات المحتوى {series_id}: {e}")
        return [], 0, 0

async def get_content_info(series_id):
    """جلب معلومات محتوى محدد"""
    cache_key = f"content_info_{series_id}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    if not engine:
        logger.warning("محرك قاعدة البيانات غير متاح")
        return None
    
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT id, name, type FROM series WHERE id = :series_id
            """), {"series_id": series_id})
            row = result.fetchone()
            if row:
                cache.set(cache_key, row)
            return row
    except Exception as e:
        logger.error(f"❌ خطأ في جلب معلومات المحتوى {series_id}: {e}")
        return None

# ==============================
# 3. دوال البوت الأساسية (يجب أن تكون موجودة!)
# ==============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /start"""
    keyboard = [
        [InlineKeyboardButton("📺 المسلسلات", callback_data='series_list'),
         InlineKeyboardButton("🎬 الأفلام", callback_data='movies_list')],
        [InlineKeyboardButton("📁 جميع المحتويات", callback_data='all_content')],
        [InlineKeyboardButton("🔄 اختبار قاعدة البيانات", callback_data='test_db')],
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_text = """
🎬 *مرحباً في بوت مسلسلاتي وأفلامي* 🎬

*مميزات البوت:*
• تصفح جميع المسلسلات في القناة
• تصفح جميع الأفلام في القناة
• الوصول السريع للحلقات والأجزاء
• تحديث تلقائي عند إضافة محتوى جديد

📌 *الأوامر المتاحة:*
/start - عرض هذه الرسالة
/series - عرض المسلسلات
/movies - عرض الأفلام
/all - عرض كل المحتويات
/test - اختبار قاعدة البيانات
/debug - فحص حالة النظام
/status - عرض حالة البوت
/clearcache - مسح التخزين المؤقت
    """
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            welcome_text,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            welcome_text,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

async def series_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /series - عرض المسلسلات"""
    await show_content(update, context, 'series')

async def movies_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /movies - عرض الأفلام"""
    await show_content(update, context, 'movie')

async def all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /all - عرض كل المحتويات"""
    await show_content(update, context)

async def show_content(update: Update, context: ContextTypes.DEFAULT_TYPE, content_type=None):
    """عرض المحتويات حسب النوع"""
    start_time = datetime.now()
    
    if not engine:
        error_msg = "❌ قاعدة البيانات غير متاحة حالياً."
        if update.callback_query:
            await update.callback_query.edit_message_text(error_msg)
        else:
            await update.message.reply_text(error_msg)
        return
    
    try:
        content_list = await get_all_content(content_type)
        
        if content_type == 'series':
            title = "📺 *قائمة المسلسلات*"
            empty_msg = "📭 لا توجد مسلسلات حالياً."
        elif content_type == 'movie':
            title = "🎬 *قائمة الأفلام*"
            empty_msg = "📭 لا توجد أفلام حالياً."
        else:
            title = "📁 *جميع المحتويات*"
            empty_msg = "📭 لا توجد محتويات حالياً."
        
        if not content_list:
            keyboard = [
                [InlineKeyboardButton("🔄 تحديث", callback_data=f"{content_type}_list" if content_type else "all_content")],
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
        
        # بناء النص
        text = f"{title}\n\n"
        keyboard = []
        
        for content in content_list:
            content_id, name, content_type, episode_count = content
            
            if content_type == 'series':
                count_text = f"{episode_count} حلقة" if episode_count > 0 else "بدون حلقات"
            else:
                count_text = f"{episode_count} جزء" if episode_count > 0 else "بدون أجزاء"
            
            text += f"{name} ({count_text})\n"
            keyboard.append([
                InlineKeyboardButton(
                    f"{name[:20]}",
                    callback_data=f"content_{content_id}"
                )
            ])
        
        # أزرار التنقل
        keyboard.append([
            InlineKeyboardButton("📺 المسلسلات", callback_data="series_list"),
            InlineKeyboardButton("🎬 الأفلام", callback_data="movies_list")
        ])
        keyboard.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="home")])
        
        # إضافة وقت الاستجابة
        response_time = (datetime.now() - start_time).total_seconds()
        text += f"\n⏱️ وقت الاستجابة: {response_time:.2f} ثانية"
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
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
            
    except Exception as e:
        logger.error(f"خطأ في عرض المحتويات: {e}")
        error_msg = f"❌ حدث خطأ: {str(e)[:100]}"
        if update.callback_query:
            await update.callback_query.edit_message_text(error_msg)
        else:
            await update.message.reply_text(error_msg)

async def show_content_details(update: Update, context: ContextTypes.DEFAULT_TYPE, content_id, page=1):
    """عرض تفاصيل محتوى محدد"""
    query = update.callback_query
    
    content_info = await get_content_info(content_id)
    if not content_info:
        await query.edit_message_text("❌ المحتوى غير موجود.")
        return
    
    content_id, name, content_type = content_info
    episodes, total_episodes, total_pages = await get_content_episodes(content_id, page)
    
    if not episodes:
        item_type = "حلقات" if content_type == 'series' else "أجزاء"
        message_text = f"*{name}*\n\n📭 لا توجد {item_type} حالياً."
        keyboard = [[InlineKeyboardButton("⬅️ رجوع", callback_data=f"{content_type}_list")]]
        await query.edit_message_text(
            message_text, 
            parse_mode='Markdown', 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # تجميع الحلقات حسب الموسم
    seasons = {}
    for ep in episodes:
        ep_id, season, ep_num, msg_id, channel_id = ep
        if season not in seasons:
            seasons[season] = []
        seasons[season].append((ep_id, ep_num, msg_id, channel_id))
    
    # بناء الرسالة
    item_type = "حلقات" if content_type == 'series' else "أجزاء"
    message_text = f"*{name}*\n\n"
    
    if total_episodes > 0:
        message_text += f"عدد {item_type}: {total_episodes}\n"
        if total_pages > 1:
            message_text += f"الصفحة {page} من {total_pages}\n\n"
    
    keyboard = []
    
    if content_type == 'series':
        if len(seasons) > 1:
            message_text += "اختر الموسم:"
            for season_num in sorted(seasons.keys()):
                ep_count = len(seasons[season_num])
                keyboard.append([
                    InlineKeyboardButton(
                        f"الموسم {season_num} ({ep_count} حلقة)",
                        callback_data=f"season_{content_id}_{season_num}"
                    )
                ])
        else:
            season_num = list(seasons.keys())[0] if seasons else 1
            season_episodes = seasons.get(season_num, [])
            
            message_text += f"الموسم {season_num}\nاختر الحلقة:"
            
            row_buttons = []
            for ep_id, ep_num, msg_id, channel_id in season_episodes:
                row_buttons.append(
                    InlineKeyboardButton(
                        f"الحلقة {ep_num}",
                        callback_data=f"ep_{ep_id}"
                    )
                )
                
                if len(row_buttons) == 5:
                    keyboard.append(row_buttons)
                    row_buttons = []
            
            if row_buttons:
                keyboard.append(row_buttons)
    else:
        if len(seasons) > 1:
            message_text += "اختر الجزء:"
            for season_num in sorted(seasons.keys()):
                ep_id, ep_num, msg_id, channel_id = seasons[season_num][0]
                keyboard.append([
                    InlineKeyboardButton(
                        f"الجزء {season_num}",
                        callback_data=f"ep_{ep_id}"
                    )
                ])
        else:
            season_num = list(seasons.keys())[0] if seasons else 1
            season_episodes = seasons.get(season_num, [])
            
            if season_episodes:
                ep_id, ep_num, msg_id, channel_id = season_episodes[0]
                message_text += "اضغط على الزر أدناه لمشاهدة الفيلم:"
                keyboard = [[
                    InlineKeyboardButton(
                        "مشاهدة الفيلم",
                        callback_data=f"ep_{ep_id}"
                    )
                ]]
    
    # أزرار التنقل بين الصفحات
    if total_pages > 1:
        nav_buttons = []
        
        if page > 1:
            nav_buttons.append(
                InlineKeyboardButton("⬅️ السابقة", callback_data=f"content_page_{content_id}_{page-1}")
            )
        
        nav_buttons.append(
            InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="page_info")
        )
        
        if page < total_pages:
            nav_buttons.append(
                InlineKeyboardButton("التالية ➡️", callback_data=f"content_page_{content_id}_{page+1}")
            )
        
        keyboard.append(nav_buttons)
    
    keyboard.append([
        InlineKeyboardButton("⬅️ رجوع", callback_data=f"{content_type}_list"),
        InlineKeyboardButton("🏠 الرئيسية", callback_data="home")
    ])
    
    await query.edit_message_text(
        message_text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_season_episodes(update: Update, context: ContextTypes.DEFAULT_TYPE, content_id, season_num, page=1):
    """عرض حلقات موسم محدد"""
    query = update.callback_query
    
    content_info = await get_content_info(content_id)
    if not content_info:
        await query.edit_message_text("❌ المحتوى غير موجود.")
        return
    
    content_id, name, content_type = content_info
    
    if content_type != 'series':
        await query.edit_message_text("❌ هذه الدالة للمسلسلات فقط.")
        return
    
    try:
        with engine.connect() as conn:
            # حساب العدد الإجمالي للحلقات للموسم
            count_result = conn.execute(text("""
                SELECT COUNT(*) FROM episodes 
                WHERE series_id = :series_id AND season = :season
            """), {"series_id": content_id, "season": season_num})
            total_episodes = count_result.scalar()
            
            # حساب عدد الصفحات
            per_page = 50
            total_pages = (total_episodes + per_page - 1) // per_page
            
            # ضبط رقم الصفحة
            if page < 1:
                page = 1
            elif page > total_pages and total_pages > 0:
                page = total_pages
            
            offset = (page - 1) * per_page
            
            # جلب الحلقات
            result = conn.execute(text("""
                SELECT e.id, e.season, e.episode_number, 
                       e.telegram_message_id, e.telegram_channel_id
                FROM episodes e
                WHERE e.series_id = :series_id AND e.season = :season
                ORDER BY e.episode_number
                LIMIT :limit OFFSET :offset
            """), {
                "series_id": content_id,
                "season": season_num,
                "limit": per_page,
                "offset": offset
            })
            
            episodes = result.fetchall()
            
    except Exception as e:
        await query.edit_message_text(f"❌ خطأ في جلب حلقات الموسم: {e}")
        return
    
    if not episodes:
        await query.edit_message_text(f"❌ لا توجد حلقات للموسم {season_num}.")
        return
    
    message_text = f"*{name}*\nالموسم {season_num}\n\n"
    
    if total_episodes > 0:
        message_text += f"عدد الحلقات: {total_episodes}\n"
        if total_pages > 1:
            message_text += f"الصفحة {page} من {total_pages}\n\n"
    
    message_text += "اختر الحلقة:"
    
    keyboard = []
    row_buttons = []
    
    for ep in episodes:
        ep_id, season, ep_num, msg_id, channel_id = ep
        row_buttons.append(
            InlineKeyboardButton(
                f"الحلقة {ep_num}",
                callback_data=f"ep_{ep_id}"
            )
        )
        
        if len(row_buttons) == 5:
            keyboard.append(row_buttons)
            row_buttons = []
    
    if row_buttons:
        keyboard.append(row_buttons)
    
    if total_pages > 1:
        nav_buttons = []
        
        if page > 1:
            nav_buttons.append(
                InlineKeyboardButton("⬅️ السابقة", callback_data=f"season_page_{content_id}_{season_num}_{page-1}")
            )
        
        nav_buttons.append(
            InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="page_info")
        )
        
        if page < total_pages:
            nav_buttons.append(
                InlineKeyboardButton("التالية ➡️", callback_data=f"season_page_{content_id}_{season_num}_{page+1}")
            )
        
        keyboard.append(nav_buttons)
    
    keyboard.append([
        InlineKeyboardButton("⬅️ رجوع للمسلسل", callback_data=f"content_{content_id}"),
        InlineKeyboardButton("🏠 الرئيسية", callback_data="home")
    ])
    
    await query.edit_message_text(
        message_text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_episode_details(update: Update, context: ContextTypes.DEFAULT_TYPE, episode_id):
    """عرض تفاصيل حلقة/جزء"""
    query = update.callback_query
    
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT e.season, e.episode_number, e.telegram_message_id,
                       s.name as series_name, s.type as series_type, s.id as series_id
                FROM episodes e
                JOIN series s ON e.series_id = s.id
                WHERE e.id = :episode_id
            """), {"episode_id": episode_id}).fetchone()
    except Exception as e:
        await query.edit_message_text(f"❌ خطأ في جلب معلومات الحلقة: {e}")
        return
    
    if not result:
        await query.edit_message_text("❌ الحلقة/الجزء غير موجود.")
        return
    
    season, episode_num, msg_id, series_name, series_type, series_id = result
    
    if msg_id:
        episode_link = f"https://t.me/ShoofFilm/{msg_id}"
        if series_type == 'series':
            link_text = f"🔗 [رابط الحلقة في القناة]({episode_link})"
            title_text = f"*{series_name}*\nالموسم {season} - الحلقة {episode_num}"
            button_text = "مشاهدة الحلقة"
        else:
            link_text = f"🔗 [رابط الجزء في القناة]({episode_link})"
            title_text = f"*{series_name}*\nالجزء {season}"
            button_text = "مشاهدة الجزء"
    else:
        episode_link = None
        link_text = "⚠️ تعذر إنشاء رابط للحلقة/الجزء."
        if series_type == 'series':
            title_text = f"*{series_name}*\nالموسم {season} - الحلقة {episode_num}"
            button_text = "مشاهدة الحلقة"
        else:
            title_text = f"*{series_name}*\nالجزء {season}"
            button_text = "مشاهدة الجزء"
    
    message_text = (
        f"{title_text}\n\n"
        f"{link_text}\n\n"
        f"*ملاحظة:* تأكد من أنك منضم للقناة لمشاهدة المحتوى."
    )
    
    keyboard = []
    if episode_link:
        keyboard.append([InlineKeyboardButton(button_text, url=episode_link)])
    
    keyboard.append([
        InlineKeyboardButton("⬅️ رجوع للمحتوى", callback_data=f"content_{series_id}"),
        InlineKeyboardButton("🏠 الرئيسية", callback_data="home")
    ])
    
    await query.edit_message_text(
        message_text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard),
        disable_web_page_preview=False
    )

async def test_db_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /test - اختبار قاعدة البيانات"""
    try:
        if not engine:
            await update.message.reply_text("❌ قاعدة البيانات غير متصلة.")
            return
        
        with engine.connect() as conn:
            # جلب جميع الجداول
            tables_result = conn.execute(text("""
                SELECT table_name FROM information_schema.tables 
                WHERE table_schema = 'public'
            """)).fetchall()
            
            tables_info = "📋 *الجداول الموجودة:*\n"
            for table in tables_result:
                table_name = table[0]
                count_result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).fetchone()
                count = count_result[0] if count_result else 0
                tables_info += f"• `{table_name}`: {count} صف\n"
            
            # جلب عينات من البيانات
            series_sample = conn.execute(text("""
                SELECT id, name, type FROM series ORDER BY id LIMIT 5
            """)).fetchall()
            
            episodes_sample = conn.execute(text("""
                SELECT id, series_id, season, episode_number FROM episodes ORDER BY id LIMIT 5
            """)).fetchall()
            
            series_text = "🎬 *عينة من المسلسلات والأفلام:*\n"
            for row in series_sample:
                series_text += f"• ID:{row[0]} - {row[1]} ({row[2]})\n"
            
            episodes_text = "📺 *عينة من الحلقات:*\n"
            for row in episodes_sample:
                episodes_text += f"• ID:{row[0]} - مسلسل:{row[1]} - م{row[2]} ح{row[3]}\n"
            
            reply_text = f"{tables_info}\n{series_text}\n{episodes_text}"
        
        await update.message.reply_text(reply_text, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ في اختبار قاعدة البيانات:\n`{str(e)[:300]}`")

async def test_db_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اختبار قاعدة البيانات من الزر"""
    query = update.callback_query
    
    try:
        if not engine:
            await query.edit_message_text("❌ قاعدة البيانات غير متصلة.")
            return
        
        with engine.connect() as conn:
            # جلب إحصائيات بسيطة
            series_count = conn.execute(text("SELECT COUNT(*) FROM series WHERE type = 'series'")).scalar()
            movies_count = conn.execute(text("SELECT COUNT(*) FROM series WHERE type = 'movie'")).scalar()
            
            # جلب بعض الأمثلة
            series_examples = conn.execute(text("""
                SELECT name FROM series WHERE type = 'series' ORDER BY id LIMIT 3
            """)).fetchall()
            
            movies_examples = conn.execute(text("""
                SELECT name FROM series WHERE type = 'movie' ORDER BY id LIMIT 3
            """)).fetchall()
        
        series_names = [row[0] for row in series_examples] if series_examples else ["لا يوجد"]
        movies_names = [row[0] for row in movies_examples] if movies_examples else ["لا يوجد"]
        
        reply_text = (
            f"✅ *اختبار قاعدة البيانات:*\n\n"
            f"📊 *الإحصائيات:*\n"
            f"• عدد المسلسلات: {series_count}\n"
            f"• عدد الأفلام: {movies_count}\n\n"
            f"📺 *أمثلة على المسلسلات:*\n"
            f"{chr(10).join(['• ' + name for name in series_names])}\n\n"
            f"🎬 *أمثلة على الأفلام:*\n"
            f"{chr(10).join(['• ' + name for name in movies_names])}\n\n"
            f"ℹ️ *ملاحظة:* إذا كانت الأرقام غير صفرية ولكن لا تظهر في القوائم، قد يكون هناك مشكلة في استعلام JOIN."
        )
        
        keyboard = [
            [InlineKeyboardButton("📺 عرض المسلسلات", callback_data="series_list"),
             InlineKeyboardButton("🎬 عرض الأفلام", callback_data="movies_list")],
            [InlineKeyboardButton("🏠 الرئيسية", callback_data="home")]
        ]
        
        await query.edit_message_text(
            reply_text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        await query.edit_message_text(f"❌ خطأ في اختبار قاعدة البيانات:\n`{str(e)[:200]}`")

async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /debug - فحص حالة النظام"""
    try:
        if not engine:
            await update.message.reply_text("❌ قاعدة البيانات غير متصلة.")
            return
        
        with engine.connect() as conn:
            # إحصائيات عامة
            series_result = conn.execute(text("SELECT COUNT(*) FROM series WHERE type = 'series'")).fetchone()
            movies_result = conn.execute(text("SELECT COUNT(*) FROM series WHERE type = 'movie'")).fetchone()
            episodes_result = conn.execute(text("SELECT COUNT(*) FROM episodes")).fetchone()
            
            # تفاصيل أكثر
            series_with_episodes = conn.execute(text("""
                SELECT s.name, s.type, COUNT(e.id) as ep_count
                FROM series s
                LEFT JOIN episodes e ON s.id = e.series_id
                GROUP BY s.id, s.name, s.type
                ORDER BY s.id ASC
                LIMIT 5
            """)).fetchall()
            
            # آخر 10 حلقات مضافة
            recent_eps = conn.execute(text("""
                SELECT s.name, s.type, e.season, e.episode_number, e.added_at
                FROM episodes e 
                JOIN series s ON e.series_id = s.id 
                ORDER BY e.id DESC 
                LIMIT 10
            """)).fetchall()
        
        series_count = series_result[0] if series_result else 0
        movies_count = movies_result[0] if movies_result else 0
        episodes_count = episodes_result[0] if episodes_result else 0
        
        series_details = "📊 *تفاصيل بعض المحتويات:*\n"
        for row in series_with_episodes:
            name, content_type, ep_count = row
            icon = "📺" if content_type == 'series' else "🎬"
            series_details += f"{icon} {name}: {ep_count} {'حلقة' if content_type == 'series' else 'جزء'}\n"
        
        recent_details = "🆕 *آخر الحلقات المضافة:*\n"
        for row in recent_eps:
            name, content_type, season, ep_num, added_at = row
            icon = "📺" if content_type == 'series' else "🎬"
            if content_type == 'series':
                recent_details += f"{icon} {name}: م{season} ح{ep_num}\n"
            else:
                recent_details += f"{icon} {name}: جزء {season}\n"
        
        reply_text = (
            f"📊 **فحص النظام:**\n"
            f"• قاعدة البيانات: {'✅ متصلة' if engine else '❌ غير متصلة'}\n"
            f"• عدد المسلسلات: `{series_count}`\n"
            f"• عدد الأفلام: `{movies_count}`\n"
            f"• إجمالي المحتويات: `{series_count + movies_count}`\n"
            f"• عدد الحلقات/الأجزاء: `{episodes_count}`\n"
            f"• حجم التخزين المؤقت: `{len(cache.cache)}` عنصر\n\n"
            f"{series_details}\n"
            f"{recent_details}"
        )
        
        await update.message.reply_text(reply_text, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ في الفحص:\n`{str(e)[:300]}`")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض حالة النظام"""
    cache_size = len(cache.cache)
    db_status = "✅ متصلة" if engine else "❌ غير متصلة"
    
    status_text = (
        f"📊 *حالة البوت:*\n\n"
        f"• قاعدة البيانات: {db_status}\n"
        f"• حجم التخزين المؤقت: {cache_size} عنصر\n"
        f"• وقت التشغيل: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"💡 *تلميح:* استخدم /clearcache لمسح التخزين المؤقت"
    )
    
    await update.message.reply_text(status_text, parse_mode='Markdown')

async def clear_cache_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مسح التخزين المؤقت"""
    cache.clear()
    await update.message.reply_text("✅ تم مسح التخزين المؤقت.")

# ==============================
# 4. معالج الأزرار التفاعلية
# ==============================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أزرار InlineKeyboard"""
    query = update.callback_query
    await query.answer()
    
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
        elif data == 'page_info':
            await query.answer("معلومات الصفحة", show_alert=False)
        elif data.startswith('content_page_'):
            parts = data.split('_')
            content_id = int(parts[2])
            page = int(parts[3])
            await show_content_details(update, context, content_id, page)
        elif data.startswith('season_page_'):
            parts = data.split('_')
            content_id = int(parts[2])
            season_num = int(parts[3])
            page = int(parts[4])
            await show_season_episodes(update, context, content_id, season_num, page)
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
        else:
            await query.answer("زر غير معروف!")
    except Exception as e:
        logger.error(f"خطأ في معالجة الزر {data}: {e}")
        await query.answer(f"حدث خطأ: {str(e)[:50]}", show_alert=True)

# ==============================
# 5. الدالة الرئيسية
# ==============================
def main():
    """الدالة الرئيسية لتشغيل البوت"""
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
            await update.effective_chat.send_message("❌ حدث خطأ في المعالجة.")
    
    application.add_error_handler(error_handler)
    
    # تشغيل البوت
    logger.info("🤖 البوت يعمل باستخدام Polling...")
    logger.info(f"✅ قاعدة البيانات: {'متصل' if engine else 'غير متصل'}")
    
    try:
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            poll_interval=0.5,
            timeout=30,
            drop_pending_updates=True
        )
    except KeyboardInterrupt:
        logger.info("إيقاف البوت...")
        if engine:
            engine.dispose()
        logger.info("تم إيقاف البوت.")

if __name__ == "__main__":
    main()
