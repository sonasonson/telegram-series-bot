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
    except Exception as e:
        print(f"❌ فشل الاتصال بقاعدة البيانات: {e}")
        engine = None

# ==============================
# 2. دوال المساعدة للتعامل مع قاعدة البيانات
# ==============================
async def get_all_content(content_type=None):
    """جلب جميع المحتويات من قاعدة البيانات حسب النوع (مسلسلات/أفلام)"""
    if not engine:
        return []
    
    try:
        with engine.connect() as conn:
            if content_type:
                # جلب محتوى نوع محدد
                result = conn.execute(text(f"""
                    SELECT s.id, s.name, s.type, COUNT(e.id) as episode_count
                    FROM series s
                    LEFT JOIN episodes e ON s.id = e.series_id
                    WHERE s.type = '{content_type}'
                    GROUP BY s.id, s.name, s.type
                    ORDER BY s.id ASC  # الأقدم أولاً
                """))
            else:
                # جلب كل المحتويات
                result = conn.execute(text("""
                    SELECT s.id, s.name, s.type, COUNT(e.id) as episode_count
                    FROM series s
                    LEFT JOIN episodes e ON s.id = e.series_id
                    GROUP BY s.id, s.name, s.type
                    ORDER BY s.id ASC  # الأقدم أولاً
                """))
            return result.fetchall()
    except Exception as e:
        print(f"❌ خطأ في جلب المحتويات: {e}")
        return []

async def get_content_episodes(series_id):
    """جلب حلقات/أجزاء محتوى محدد"""
    if not engine:
        return []
    
    try:
        with engine.connect() as conn:
            # جلب الحلقات مرتبة بالموسم ورقم الحلقة
            result = conn.execute(text("""
                SELECT e.id, e.season, e.episode_number, 
                       e.telegram_message_id, e.telegram_channel_id
                FROM episodes e
                WHERE e.series_id = :series_id
                ORDER BY e.season, e.episode_number
            """), {"series_id": series_id})
            return result.fetchall()
    except Exception as e:
        print(f"❌ خطأ في جلب حلقات المحتوى {series_id}: {e}")
        return []

async def get_content_info(series_id):
    """جلب معلومات محتوى محدد"""
    if not engine:
        return None
    
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT id, name, type FROM series WHERE id = :series_id
            """), {"series_id": series_id})
            return result.fetchone()
    except Exception as e:
        print(f"❌ خطأ في جلب معلومات المحتوى {series_id}: {e}")
        return None

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
        no_data_msg = empty_msg
        if update.callback_query:
            await update.callback_query.edit_message_text(no_data_msg)
        else:
            await update.message.reply_text(no_data_msg)
        return
    
    # بناء النص
    text = f"{title}\n\n"
    keyboard = []
    
    series_count = 0
    movies_count = 0
    
    for content in content_list:
        content_id, name, content_type, episode_count = content
        
        if content_type == 'series':
            series_count += 1
            type_icon = "📺"
            count_text = f"{episode_count} حلقة"
        else:
            movies_count += 1
            type_icon = "🎬"
            count_text = f"{episode_count} جزء"
        
        text += f"{type_icon} {name} ({count_text})\n"
        keyboard.append([
            InlineKeyboardButton(
                f"{type_icon} {name[:15]}",
                callback_data=f"content_{content_id}"
            )
        ])
    
    # إضافة إحصائيات إذا كان عرض الكل
    if not content_type:
        text += f"\n📊 *الإحصائيات:*\n"
        text += f"• عدد المسلسلات: {series_count}\n"
        text += f"• عدد الأفلام: {movies_count}\n"
        text += f"• المجموع: {len(content_list)}"
    
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
            
            # عينات من المسلسلات والأفلام
            sample_series = conn.execute(text("SELECT name FROM series WHERE type = 'series' LIMIT 3")).fetchall()
            sample_movies = conn.execute(text("SELECT name FROM series WHERE type = 'movie' LIMIT 3")).fetchall()
            
            # آخر محتويات مضافة
            recent_content = conn.execute(text("""
                SELECT s.name, s.type, e.episode_number, e.season
                FROM episodes e 
                JOIN series s ON e.series_id = s.id 
                ORDER BY e.id DESC 
                LIMIT 3
            """)).fetchall()
        
        series_count = series_result[0] if series_result else 0
        movies_count = movies_result[0] if movies_result else 0
        episodes_count = episodes_result[0] if episodes_result else 0
        
        sample_series_names = [row[0] for row in sample_series] if sample_series else ["لا يوجد"]
        sample_movies_names = [row[0] for row in sample_movies] if sample_movies else ["لا يوجد"]
        
        recent_items = []
        for row in recent_content:
            name, content_type, ep_num, season = row
            if content_type == 'series':
                recent_items.append(f"{name} (م{season} ح{ep_num})")
            else:
                recent_items.append(f"{name} (ج{season})")
        
        if not recent_items:
            recent_items = ["لا يوجد"]
        
        reply_text = (
            f"📊 **فحص النظام:**\n"
            f"• قاعدة البيانات: {'✅ متصلة' if engine else '❌ غير متصلة'}\n"
            f"• عدد المسلسلات: `{series_count}`\n"
            f"• عدد الأفلام: `{movies_count}`\n"
            f"• إجمالي المحتويات: `{series_count + movies_count}`\n"
            f"• عدد الحلقات/الأجزاء: `{episodes_count}`\n\n"
            f"📺 *عينة من المسلسلات:*\n`{', '.join(sample_series_names)}`\n\n"
            f"🎬 *عينة من الأفلام:*\n`{', '.join(sample_movies_names)}`\n\n"
            f"🆕 *آخر المحتويات المضافة:*\n`{', '.join(recent_items)}`"
        )
        
        await update.message.reply_text(reply_text, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ في الفحص:\n`{str(e)[:200]}`")

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
    application.add_handler(CommandHandler("debug", debug_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # تشغيل البوت
    print("🤖 البوت يعمل باستخدام Polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
