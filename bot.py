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
            
            result = conn.execute(text(query))
            rows = result.fetchall()
            return rows
            
    except Exception as e:
        print(f"❌ خطأ في جلب المحتويات: {e}")
        import traceback
        traceback.print_exc()
        return []

async def get_content_episodes(series_id, page=1, per_page=50):
    """جلب حلقات/أجزاء محتوى محدد مع دعم التقسيم إلى صفحات"""
    if not engine:
        print("⚠️ محرك قاعدة البيانات غير متاح في get_content_episodes")
        return [], 0, 0
    
    try:
        with engine.connect() as conn:
            # حساب العدد الإجمالي للحلقات
            count_result = conn.execute(text("""
                SELECT COUNT(*) FROM episodes WHERE series_id = :series_id
            """), {"series_id": series_id})
            total_episodes = count_result.scalar()
            
            # حساب عدد الصفحات
            total_pages = (total_episodes + per_page - 1) // per_page
            
            # ضبط رقم الصفحة إذا كان خارج النطاق
            if page < 1:
                page = 1
            elif page > total_pages and total_pages > 0:
                page = total_pages
            
            # حساب offset للصفحة
            offset = (page - 1) * per_page
            
            # جلب الحلقات للصفحة الحالية
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
            return rows, total_episodes, total_pages
            
    except Exception as e:
        print(f"❌ خطأ في جلب حلقات المحتوى {series_id}: {e}")
        return [], 0, 0

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
            return row
    except Exception as e:
        print(f"❌ خطأ في جلب معلومات المحتوى {series_id}: {e}")
        return None

# ==============================
# 3. دوال البوت الرئيسية مع دعم التقسيم إلى صفحات
# ==============================
async def show_content_details(update: Update, context: ContextTypes.DEFAULT_TYPE, content_id, page=1):
    """عرض تفاصيل محتوى محدد مع دعم التقسيم إلى صفحات"""
    query = update.callback_query
    
    # جلب معلومات المحتوى
    content_info = await get_content_info(content_id)
    if not content_info:
        await query.edit_message_text("❌ المحتوى غير موجود.")
        return
    
    content_id, name, content_type = content_info
    episodes, total_episodes, total_pages = await get_content_episodes(content_id, page)
    
    type_icon = "📺" if content_type == 'series' else "🎬"
    per_page = 50  # عدد الحلقات في الصفحة الواحدة
    
    if not episodes:
        item_type = "حلقات" if content_type == 'series' else "أجزاء"
        message_text = f"{type_icon} *{name}*\n\n📭 لا توجد {item_type} حالياً."
        keyboard = [[InlineKeyboardButton("⬅️ رجوع", callback_data=f"{content_type}_list")]]
        await query.edit_message_text(
            message_text, 
            parse_mode='Markdown', 
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # تجميع الحلقات حسب الموسم (للمسلسلات) أو الجزء (للأفلام)
    seasons = {}
    for ep in episodes:
        ep_id, season, ep_num, msg_id, channel_id = ep
        if season not in seasons:
            seasons[season] = []
        seasons[season].append((ep_id, ep_num, msg_id, channel_id))
    
    # بناء الرسالة
    item_type = "حلقات" if content_type == 'series' else "أجزاء"
    message_text = f"{type_icon} *{name}*\n\n"
    
    if total_episodes > 0:
        message_text += f"عدد {item_type}: {total_episodes}\n"
        if total_pages > 1:
            message_text += f"الصفحة {page} من {total_pages}\n\n"
    
    keyboard = []
    
    # ============================================
    # معالجة المسلسلات
    # ============================================
    if content_type == 'series':
        # إذا كان المسلسل له أكثر من موسم، نعرض قائمة المواسم
        if len(seasons) > 1:
            message_text += "اختر الموسم:"
            for season_num in sorted(seasons.keys()):
                # حساب عدد الحلقات في هذا الموسم
                ep_count = len(seasons[season_num])
                keyboard.append([
                    InlineKeyboardButton(
                        f"📁 الموسم {season_num} ({ep_count} حلقة)",
                        callback_data=f"season_{content_id}_{season_num}"
                    )
                ])
        else:
            # إذا كان المسلسل له موسم واحد فقط، نعرض الحلقات مباشرة
            season_num = list(seasons.keys())[0] if seasons else 1
            season_episodes = seasons.get(season_num, [])
            
            message_text += f"الموسم {season_num}\nاختر الحلقة:"
            
            # تقسيم أزرار الحلقات (5 أزرار في كل صف)
            row_buttons = []
            for ep_id, ep_num, msg_id, channel_id in season_episodes:
                row_buttons.append(
                    InlineKeyboardButton(
                        f"▶️ {ep_num}",
                        callback_data=f"ep_{ep_id}"
                    )
                )
                
                # كل 5 أزرار نبدأ صف جديد
                if len(row_buttons) == 5:
                    keyboard.append(row_buttons)
                    row_buttons = []
            
            if row_buttons:
                keyboard.append(row_buttons)
    
    # ============================================
    # معالجة الأفلام
    # ============================================
    else:  # content_type == 'movie'
        # إذا كان الفيلم له أكثر من جزء
        if len(seasons) > 1:
            message_text += "اختر الجزء:"
            for season_num in sorted(seasons.keys()):
                # لكل جزء (موسم) نأخذ الحلقة الأولى (والوحيدة)
                ep_id, ep_num, msg_id, channel_id = seasons[season_num][0]
                keyboard.append([
                    InlineKeyboardButton(
                        f"🎬 الجزء {season_num}",
                        callback_data=f"ep_{ep_id}"
                    )
                ])
        else:
            # إذا كان الفيلم له جزء واحد فقط
            season_num = list(seasons.keys())[0] if seasons else 1
            season_episodes = seasons.get(season_num, [])
            
            if season_episodes:
                ep_id, ep_num, msg_id, channel_id = season_episodes[0]
                message_text += "🎬 اضغط على الزر أدناه لمشاهدة الفيلم:"
                keyboard = [[
                    InlineKeyboardButton(
                        "🎬 مشاهدة الفيلم",
                        callback_data=f"ep_{ep_id}"
                    )
                ]]
    
    # أزرار التنقل بين الصفحات إذا كان هناك أكثر من صفحة
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
    
    # أزرار التنقل الرئيسية
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
    """عرض حلقات موسم محدد مع دعم التقسيم إلى صفحات"""
    query = update.callback_query
    
    # جلب معلومات المحتوى
    content_info = await get_content_info(content_id)
    if not content_info:
        await query.edit_message_text("❌ المحتوى غير موجود.")
        return
    
    content_id, name, content_type = content_info
    
    # هذه الدالة للمسلسلات فقط
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
            
            # حساب offset
            offset = (page - 1) * per_page
            
            # جلب الحلقات للصفحة الحالية
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
    
    message_text = f"📺 *{name}*\n📁 الموسم {season_num}\n\n"
    
    if total_episodes > 0:
        message_text += f"عدد الحلقات: {total_episodes}\n"
        if total_pages > 1:
            message_text += f"الصفحة {page} من {total_pages}\n\n"
    
    message_text += "اختر الحلقة:"
    
    keyboard = []
    
    # تقسيم أزرار الحلقات (5 أزرار في كل صف)
    row_buttons = []
    for ep in episodes:
        ep_id, season, ep_num, msg_id, channel_id = ep
        row_buttons.append(
            InlineKeyboardButton(
                f"▶️ {ep_num}",
                callback_data=f"ep_{ep_id}"
            )
        )
        
        if len(row_buttons) == 5:
            keyboard.append(row_buttons)
            row_buttons = []
    
    if row_buttons:
        keyboard.append(row_buttons)
    
    # أزرار التنقل بين الصفحات إذا كان هناك أكثر من صفحة
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
    
    # أزرار التنقل
    keyboard.append([
        InlineKeyboardButton("⬅️ رجوع للمسلسل", callback_data=f"content_{content_id}"),
        InlineKeyboardButton("🏠 الرئيسية", callback_data="home")
    ])
    
    await query.edit_message_text(
        message_text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ==============================
# 4. معالج الأزرار التفاعلية المحدث
# ==============================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أزرار InlineKeyboard"""
    query = update.callback_query
    await query.answer()
    
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
    
    elif data == 'page_info':
        await query.answer("معلومات الصفحة", show_alert=False)
        return
    
    elif data.startswith('content_page_'):
        # بيانات الزر: content_page_<content_id>_<page_number>
        parts = data.split('_')
        content_id = int(parts[2])
        page = int(parts[3])
        await show_content_details(update, context, content_id, page)
        return
    
    elif data.startswith('season_page_'):
        # بيانات الزر: season_page_<content_id>_<season_num>_<page_number>
        parts = data.split('_')
        content_id = int(parts[2])
        season_num = int(parts[3])
        page = int(parts[4])
        await show_season_episodes(update, context, content_id, season_num, page)
        return
    
    elif data.startswith('content_'):
        content_id = int(data.split('_')[1])
        await show_content_details(update, context, content_id, 1)  # الصفحة الأولى
        return
    
    elif data.startswith('ep_'):
        episode_id = int(data.split('_')[1])
        await show_episode_details(update, context, episode_id)
        return
    
    elif data.startswith('season_'):
        # بيانات الزر: season_<content_id>_<season_number>
        parts = data.split('_')
        content_id = int(parts[1])
        season_num = int(parts[2])
        await show_season_episodes(update, context, content_id, season_num, 1)  # الصفحة الأولى
        return

# ==============================
# 5. تحديث باقي الدوال (بدون تغيير)
# ==============================
# باقي الدوال (start, show_content, series_command, movies_command, 
# all_command, test_db_command, debug_command, show_episode_details, 
# test_db_button) تبقى كما هي بدون تغيير
# ... (نفس الكود الأصلي)

# ==============================
# 6. الدالة الرئيسية
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
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # تشغيل البوت
    print("🤖 البوت يعمل باستخدام Polling...")
    print(f"✅ تم الاتصال بقاعدة البيانات: {engine is not None}")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
