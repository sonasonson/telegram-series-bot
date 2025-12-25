import os
import logging
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

# محرك قاعدة البيانات
engine = None
if DATABASE_URL:
    try:
        engine = create_engine(DATABASE_URL)
        # اختبار الاتصال
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("✅ تم الاتصال بقاعدة البيانات بنجاح.")
        
        # اختبار جلب البيانات مباشرة
        with engine.connect() as conn:
            series_count = conn.execute(text("SELECT COUNT(*) FROM series WHERE type = 'series'")).scalar()
            movies_count = conn.execute(text("SELECT COUNT(*) FROM series WHERE type = 'movie'")).scalar()
            print(f"📊 في الاختبار المبدئي:")
            print(f"   - عدد المسلسلات: {series_count}")
            print(f"   - عدد الأفلام: {movies_count}")
            
    except Exception as e:
        print(f"❌ فشل الاتصال بقاعدة البيانات: {e}")
        engine = None

# ==============================
# 2. دوال المساعدة للتعامل مع قاعدة البيانات
# ==============================
async def get_all_content(content_type=None):
    """جلب جميع المحتويات من قاعدة البيانات حسب النوع (مسلسلات/أفلام)"""
    if not engine:
        print("⚠️ محرك قاعدة البيانات غير متاح في get_all_content")
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
            
            print(f"🔍 تنفيذ الاستعلام: {query[:100]}...")
            result = conn.execute(text(query))
            rows = result.fetchall()
            
            print(f"📊 تم جلب {len(rows)} صفاً من قاعدة البيانات:")
            for row in rows:
                print(f"   - {row[1]} ({row[2]}) - {row[3]} حلقة/جزء")
            
            return rows
            
    except Exception as e:
        print(f"❌ خطأ في جلب المحتويات: {e}")
        import traceback
        traceback.print_exc()
        return []

async def get_content_episodes(series_id):
    """جلب حلقات/أجزاء محتوى محدد"""
    if not engine:
        print("⚠️ محرك قاعدة البيانات غير متاح في get_content_episodes")
        return []
    
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT e.id, e.season, e.episode_number, 
                       e.telegram_message_id, e.telegram_channel_id
                FROM episodes e
                WHERE e.series_id = :series_id
                ORDER BY e.season, e.episode_number
            """), {"series_id": series_id})
            rows = result.fetchall()
            print(f"🔍 تم جلب {len(rows)} حلقة/جزء للمحتوى {series_id}")
            return rows
    except Exception as e:
        print(f"❌ خطأ في جلب حلقات المحتوى {series_id}: {e}")
        return []

async def get_content_info(series_id):
    """جلب معلومات محتوى محدد"""
    if not engine:
        print("⚠️ محرك قاعدة البيانات غير متاح في get_content_info")
        return None
    
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT id, name, type FROM series WHERE id = :series_id
            """), {"series_id": series_id})
            row = result.fetchone()
            if row:
                print(f"🔍 معلومات المحتوى {series_id}: {row[1]} ({row[2]})")
            return row
    except Exception as e:
        print(f"❌ خطأ في جلب معلومات المحتوى {series_id}: {e}")
        return None

async def get_direct_data():
    """جلب البيانات مباشرة بدون JOIN للمقارنة"""
    if not engine:
        return [], []
    
    try:
        with engine.connect() as conn:
            # جلب المسلسلات
            series_result = conn.execute(text("""
                SELECT id, name FROM series WHERE type = 'series' ORDER BY id ASC
            """))
            series = series_result.fetchall()
            
            # جلب الأفلام
            movies_result = conn.execute(text("""
                SELECT id, name FROM series WHERE type = 'movie' ORDER BY id ASC
            """))
            movies = movies_result.fetchall()
            
            print(f"📊 البيانات المباشرة:")
            print(f"   - عدد المسلسلات: {len(series)}")
            print(f"   - عدد الأفلام: {len(movies)}")
            
            return series, movies
            
    except Exception as e:
        print(f"❌ خطأ في جلب البيانات المباشرة: {e}")
        return [], []

# ==============================
# 3. دوال البوت الرئيسية
# ==============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /start"""
    keyboard = [
        [InlineKeyboardButton("📺 المسلسلات", callback_data='series_list'),
         InlineKeyboardButton("🎬 الأفلام", callback_data='movies_list')],
        [InlineKeyboardButton("📁 جميع المحتويات", callback_data='all_content')],
        [InlineKeyboardButton("🔍 بحث سريع", switch_inline_query_current_chat='')],
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

async def show_content(update: Update, context: ContextTypes.DEFAULT_TYPE, content_type=None):
    """عرض المحتويات حسب النوع"""
    if not engine:
        error_msg = "❌ قاعدة البيانات غير متاحة حالياً."
        if update.callback_query:
            await update.callback_query.edit_message_text(error_msg)
        else:
            await update.message.reply_text(error_msg)
        return
    
    content_list = await get_all_content(content_type)
    
    if content_type == 'series':
        title = "📺 *قائمة المسلسلات*"
        empty_msg = "📭 لا توجد مسلسلات حالياً."
        item_type = "مسلسل"
        item_icon = "📺"
    elif content_type == 'movie':
        title = "🎬 *قائمة الأفلام*"
        empty_msg = "📭 لا توجد أفلام حالياً."
        item_type = "فيلم"
        item_icon = "🎬"
    else:
        title = "📁 *جميع المحتويات*"
        empty_msg = "📭 لا توجد محتويات حالياً."
        item_type = "محتوى"
        item_icon = "📁"
    
    if not content_list:
        # محاولة جلب البيانات مباشرة للتحقق
        series, movies = await get_direct_data()
        
        if content_type == 'series' and series:
            # بناء قائمة المسلسلات مباشرة
            text = f"{title}\n\n"
            keyboard = []
            for s in series:
                text += f"📺 {s[1]}\n"
                keyboard.append([
                    InlineKeyboardButton(
                        f"📺 {s[1][:15]}",
                        callback_data=f"content_{s[0]}"
                    )
                ])
            
            keyboard.append([
                InlineKeyboardButton("📺 المسلسلات", callback_data="series_list"),
                InlineKeyboardButton("🎬 الأفلام", callback_data="movies_list")
            ])
            keyboard.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="home")])
            
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
            return
            
        elif content_type == 'movie' and movies:
            # بناء قائمة الأفلام مباشرة
            text = f"{title}\n\n"
            keyboard = []
            for m in movies:
                text += f"🎬 {m[1]}\n"
                keyboard.append([
                    InlineKeyboardButton(
                        f"🎬 {m[1][:15]}",
                        callback_data=f"content_{m[0]}"
                    )
                ])
            
            keyboard.append([
                InlineKeyboardButton("📺 المسلسلات", callback_data="series_list"),
                InlineKeyboardButton("🎬 الأفلام", callback_data="movies_list")
            ])
            keyboard.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="home")])
            
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
            return
        else:
            # لا توجد بيانات حتى مباشرة
            no_data_msg = f"{empty_msg}\n\nℹ️ *ملاحظة:* يمكنك استخدام زر 'اختبار قاعدة البيانات' للتحقق."
            if update.callback_query:
                await update.callback_query.edit_message_text(no_data_msg)
            else:
                await update.message.reply_text(no_data_msg)
            return
    
    # بناء النص
    text = f"{title}\n\n"
    keyboard = []
    
    for content in content_list:
        content_id, name, content_type, episode_count = content
        
        if content_type == 'series':
            type_icon = "📺"
            count_text = f"{episode_count} حلقة" if episode_count > 0 else "بدون حلقات"
        else:
            type_icon = "🎬"
            count_text = f"{episode_count} جزء" if episode_count > 0 else "بدون أجزاء"
        
        text += f"{type_icon} {name} ({count_text})\n"
        keyboard.append([
            InlineKeyboardButton(
                f"{type_icon} {name[:15]}...",
                callback_data=f"content_{content_id}"
            )
        ])
    
    # أزرار التنقل
    keyboard.append([
        InlineKeyboardButton("📺 المسلسلات", callback_data="series_list"),
        InlineKeyboardButton("🎬 الأفلام", callback_data="movies_list")
    ])
    keyboard.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="home")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # الإرسال حسب مصدر الطلب
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

async def series_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /series - عرض المسلسلات"""
    await show_content(update, context, 'series')

async def movies_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /movies - عرض الأفلام"""
    await show_content(update, context, 'movie')

async def all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /all - عرض كل المحتويات"""
    await show_content(update, context)

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
            f"• عدد الحلقات/الأجزاء: `{episodes_count}`\n\n"
            f"{series_details}\n"
            f"{recent_details}"
        )
        
        await update.message.reply_text(reply_text, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ في الفحص:\n`{str(e)[:300]}`")

# ==============================
# 4. معالج الأزرار التفاعلية
# ==============================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أزرار InlineKeyboard"""
    query = update.callback_query
    await query.answer()  # مهم لإعلام تليجرام
    
    data = query.data
    
    if data == 'home':
        await start(update, context)
        return
    
    elif data == 'test_db':
        await test_db_button(update, context)
        return
    
    elif data == 'all_content':
        await show_content(update, context)
        return
    
    elif data == 'series_list':
        await show_content(update, context, 'series')
        return
    
    elif data == 'movies_list':
        await show_content(update, context, 'movie')
        return
    
    elif data.startswith('content_'):
        content_id = int(data.split('_')[1])
        await show_content_details(update, context, content_id)
        return
    
    elif data.startswith('ep_'):
        episode_id = int(data.split('_')[1])
        await show_episode_details(update, context, episode_id)
        return

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

async def show_content_details(update: Update, context: ContextTypes.DEFAULT_TYPE, content_id):
    """عرض تفاصيل محتوى محدد (مسلسل أو فيلم)"""
    query = update.callback_query
    
    # جلب معلومات المحتوى
    content_info = await get_content_info(content_id)
    if not content_info:
        await query.edit_message_text("❌ المحتوى غير موجود.")
        return
    
    content_id, name, content_type = content_info
    episodes = await get_content_episodes(content_id)
    
    type_arabic = "مسلسل" if content_type == 'series' else "فيلم"
    type_icon = "📺" if content_type == 'series' else "🎬"
    
    if not episodes:
        message_text = f"{type_icon} *{name}*\n\n📭 لا توجد { 'حلقات' if content_type == 'series' else 'أجزاء' } حالياً."
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
    
    # بناء النص
    message_text = f"{type_icon} *{name}*\n\n"
    keyboard = []
    
    for season_num in sorted(seasons.keys()):
        if content_type == 'series':
            message_text += f"📁 *الموسم {season_num}:*\n"
        else:
            message_text += f"📁 *الجزء {season_num}:*\n"
        
        # تقسيم أزرار الحلقات (5 أزرار في كل صف)
        season_buttons = []
        for ep_id, ep_num, msg_id, channel_id in seasons[season_num]:
            season_buttons.append(
                InlineKeyboardButton(
                    f"{ep_num}",
                    callback_data=f"ep_{ep_id}"
                )
            )
            
            # كل 5 أزرار نبدأ صف جديد
            if len(season_buttons) == 5:
                keyboard.append(season_buttons)
                season_buttons = []
        
        if season_buttons:
            keyboard.append(season_buttons)
    
    # أزرار التنقل
    keyboard.append([
        InlineKeyboardButton("⬅️ رجوع", callback_data=f"{content_type}_list"),
        InlineKeyboardButton("🏠 الرئيسية", callback_data="home")
    ])
    
    await query.edit_message_text(
        message_text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_episode_details(update: Update, context: ContextTypes.DEFAULT_TYPE, episode_id):
    """عرض تفاصيل حلقة/جزء مع روابط"""
    query = update.callback_query
    
    try:
        with engine.connect() as conn:
            from sqlalchemy import text as sql_text
            result = conn.execute(sql_text("""
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
    
    # بناء الرابط
    if msg_id:
        episode_link = f"https://t.me/ShoofFilm/{msg_id}"
        if series_type == 'series':
            link_text = f"🔗 [رابط الحلقة في القناة]({episode_link})"
        else:
            link_text = f"🔗 [رابط الجزء في القناة]({episode_link})"
    else:
        episode_link = None
        link_text = "⚠️ تعذر إنشاء رابط للحلقة."
    
    if series_type == 'series':
        message_text = (
            f"🎬 *{series_name}*\n"
            f"📁 الموسم {season} - الحلقة {episode_num}\n\n"
            f"{link_text}\n\n"
            f"*ملاحظة:* تأكد من أنك منضم للقناة لمشاهدة الحلقة."
        )
    else:
        message_text = (
            f"🎬 *{series_name}*\n"
            f"📁 الجزء {season}\n\n"
            f"{link_text}\n\n"
            f"*ملاحظة:* تأكد من أنك منضم للقناة لمشاهدة الجزء."
        )
    
    # بناء لوحة المفاتيح
    keyboard = []
    if episode_link:
        if series_type == 'series':
            keyboard.append([InlineKeyboardButton("▶️ مشاهدة الحلقة", url=episode_link)])
        else:
            keyboard.append([InlineKeyboardButton("▶️ مشاهدة الجزء", url=episode_link)])
    
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

# ==============================
# 5. الدالة الرئيسية
# ==============================
def main():
    """الدالة الرئيسية لتشغيل البوت"""
    # إنشاء تطبيق البوت
    application = Application.builder().token(BOT_TOKEN).build()
    
    # إضافة Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("series", series_command))
    application.add_handler(CommandHandler("movies", movies_command))
    application.add_handler(CommandHandler("all", all_command))
    application.add_handler(CommandHandler("test", test_db_command))
    application.add_handler(CommandHandler("debug", debug_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # تشغيل البوت
    print("🤖 البوت يعمل باستخدام Polling...")
    print(f"✅ تم الاتصال بقاعدة البيانات: {engine is not None}")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
