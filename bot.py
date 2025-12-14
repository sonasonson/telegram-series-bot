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
    print("⚠️ تحذير: DATABASE_URL غير موجود. قد لا تعرض المسلسلات.")

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
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("✅ تم الاتصال بقاعدة البيانات بنجاح.")
    except Exception as e:
        print(f"❌ فشل الاتصال بقاعدة البيانات: {e}")
        engine = None

# ==============================
# 2. دوال المساعدة للتعامل مع قاعدة البيانات
# ==============================
async def get_all_series():
    """جلب جميع المسلسلات من قاعدة البيانات مرتبة حسب id (الأقدم أولاً)"""
    if not engine:
        return []
    
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT s.id, s.name, COUNT(e.id) as episode_count
                FROM series s
                LEFT JOIN episodes e ON s.id = e.series_id
                GROUP BY s.id, s.name
                ORDER BY s.id ASC  -- ترتيب حسب id تصاعدياً
            """))
            return result.fetchall()
    except Exception as e:
        print(f"❌ خطأ في جلب المسلسلات: {e}")
        return []

async def get_series_alphabetical():
    """جلب جميع المسلسلات من قاعدة البيانات مرتبة أبجدياً"""
    if not engine:
        return []
    
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT s.id, s.name, COUNT(e.id) as episode_count
                FROM series s
                LEFT JOIN episodes e ON s.id = e.series_id
                GROUP BY s.id, s.name
                ORDER BY s.name ASC
            """))
            return result.fetchall()
    except Exception as e:
        print(f"❌ خطأ في جلب المسلسلات أبجدياً: {e}")
        return []

async def get_series_by_id_desc():
    """جلب جميع المسلسلات من قاعدة البيانات مرتبة حسب id تنازلياً (الأحدث أولاً)"""
    if not engine:
        return []
    
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT s.id, s.name, COUNT(e.id) as episode_count
                FROM series s
                LEFT JOIN episodes e ON s.id = e.series_id
                GROUP BY s.id, s.name
                ORDER BY s.id DESC  -- ترتيب حسب id تنازلياً
            """))
            return result.fetchall()
    except Exception as e:
        print(f"❌ خطأ في جلب المسلسلات (حسب id تنازلياً): {e}")
        return []

async def get_series_episodes(series_id):
    """جلب حلقات مسلسل محدد"""
    if not engine:
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
            return result.fetchall()
    except Exception as e:
        print(f"❌ خطأ في جلب حلقات المسلسل {series_id}: {e}")
        return []

# ==============================
# 3. دوال البوت الرئيسية - مُعدلة
# ==============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /start - مُعدل ليعمل في جميع الحالات"""
    keyboard = [
        [InlineKeyboardButton("📺 جميع المسلسلات", callback_data='all_series')],
        [InlineKeyboardButton("🔍 بحث سريع", switch_inline_query_current_chat='')],
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_text = """
🎬 *مرحباً في بوت مسلسلاتي* 🎬

*مميزات البوت:*
• تصفح جميع المسلسلات في القناة
• الوصول السريع للحلقات
• تحديث تلقائي عند إضافة حلقات جديدة

📌 *الأوامر المتاحة:*
/start - عرض هذه الرسالة
/series - عرض جميع المسلسلات
/debug - فحص حالة النظام
    """
    
    # إرسال الرسالة بالطريقة الصحيحة بناءً على نوع الطلب
    try:
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                welcome_text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        elif update.message:
            await update.message.reply_text(
                welcome_text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        else:
            logger.error("❌ نوع غير معروف للـ update")
    except Exception as e:
        logger.error(f"❌ خطأ في دالة start: {e}")
        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=welcome_text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )

async def show_series(update: Update, context: ContextTypes.DEFAULT_TYPE, sort_by="id_asc"):
    """عرض جميع المسلسلات مع خيارات الترتيب"""
    if not engine:
        error_msg = "❌ قاعدة البيانات غير متاحة حالياً."
        
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(error_msg)
        elif update.message:
            await update.message.reply_text(error_msg)
        return
    
    # تحديد طريقة الترتيب
    if sort_by == "alphabetical":
        series_list = await get_series_alphabetical()
        title = "📺 *قائمة المسلسلات (أبجدي)*\n\n"
        sort_button_text = "🔤 أبجدي"
        other_sort_text = "🆔 بالرقم"
        other_sort_data = "sort_id_asc"
    elif sort_by == "id_desc":
        series_list = await get_series_by_id_desc()
        title = "📺 *قائمة المسلسلات (الأحدث أولاً)*\n\n"
        sort_button_text = "🆔 الأحدث"
        other_sort_text = "🆔 الأقدم"
        other_sort_data = "sort_id_asc"
    else:  # الافتراضي: حسب id تصاعدياً (الأقدم أولاً)
        series_list = await get_all_series()
        title = "📺 *قائمة المسلسلات (حسب الإضافة)*\n\n"
        sort_button_text = "🆔 الأقدم"
        other_sort_text = "🆔 الأحدث"
        other_sort_data = "sort_id_desc"
    
    if not series_list:
        no_data_msg = "📭 لا توجد مسلسلات حالياً."
        
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(no_data_msg)
        elif update.message:
            await update.message.reply_text(no_data_msg)
        return
    
    # بناء النص
    text = title
    keyboard = []
    
    for series in series_list:
        series_id, name, episode_count = series
        
        # عرض اسم المسلسل بالكامل
        text += f"• {name} ({episode_count} حلقة)\n"
        
        # إنشاء زر المسلسل
        button_text = f"{name}"
        if len(button_text) > 25:
            button_text = button_text[:22] + "..."
        button_text += f" ({episode_count})"
        
        keyboard.append([
            InlineKeyboardButton(
                button_text,
                callback_data=f"series_{series_id}"
            )
        ])
    
    # أزرار التنقل والترتيب
    keyboard.append([
        InlineKeyboardButton(sort_button_text, callback_data=f"sort_{sort_by}"),
        InlineKeyboardButton(other_sort_text, callback_data=other_sort_data),
    ])
    
    if sort_by != "alphabetical":
        keyboard.append([InlineKeyboardButton("🔤 أبجدي", callback_data="sort_alphabetical")])
    
    keyboard.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="home")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # الإرسال حسب مصدر الطلب
    if update.callback_query:
        try:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"❌ خطأ في تعديل الرسالة: {e}")
            await update.callback_query.message.reply_text(
                text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
    elif update.message:
        await update.message.reply_text(
            text,
            parse_mode='Markdown',
            reply_markup=reply_markup
        )

async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /debug - فحص حالة النظام"""
    try:
        if not engine:
            await update.message.reply_text("❌ قاعدة البيانات غير متصلة.")
            return
        
        with engine.connect() as conn:
            # إحصائيات المسلسلات
            series_result = conn.execute(text("SELECT COUNT(*) FROM series")).fetchone()
            episodes_result = conn.execute(text("SELECT COUNT(*) FROM episodes")).fetchone()
            
            # أول 5 مسلسلات (حسب الترتيب)
            first_series = conn.execute(text("""
                SELECT id, name FROM series 
                ORDER BY id ASC 
                LIMIT 5
            """)).fetchall()
            
            # آخر 5 مسلسلات مضافة
            last_series = conn.execute(text("""
                SELECT id, name FROM series 
                ORDER BY id DESC 
                LIMIT 5
            """)).fetchall()
        
        series_count = series_result[0] if series_result else 0
        episodes_count = episodes_result[0] if episodes_result else 0
        
        first_series_text = "\n".join([f"  {row[0]}. {row[1]}" for row in first_series])
        last_series_text = "\n".join([f"  {row[0]}. {row[1]}" for row in last_series])
        
        reply_text = (
            f"📊 **فحص النظام:**\n"
            f"• قاعدة البيانات: {'✅ متصلة' if engine else '❌ غير متصلة'}\n"
            f"• عدد المسلسلات: `{series_count}`\n"
            f"• عدد الحلقات: `{episodes_count}`\n\n"
            f"• **أول 5 مسلسلات (حسب ID):**\n{first_series_text}\n\n"
            f"• **آخر 5 مسلسلات (حسب ID):**\n{last_series_text}"
        )
        
        await update.message.reply_text(reply_text, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ في الفحص:\n`{str(e)[:200]}`")

# ==============================
# 4. معالج الأزرار التفاعلية - مُعدل
# ==============================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أزرار InlineKeyboard - مُعدل"""
    query = update.callback_query
    
    # الرد على callback أولاً (مهم جداً)
    await query.answer()
    
    data = query.data
    logger.info(f"🔘 زر مضغوط: {data}")
    
    try:
        if data == 'home':
            await start(update, context)
            return
        
        elif data == 'all_series':
            await show_series(update, context, sort_by="id_asc")
            return
        
        elif data == 'sort_id_asc':
            await show_series(update, context, sort_by="id_asc")
            return
        
        elif data == 'sort_id_desc':
            await show_series(update, context, sort_by="id_desc")
            return
        
        elif data == 'sort_alphabetical':
            await show_series(update, context, sort_by="alphabetical")
            return
        
        elif data.startswith('series_'):
            series_id = int(data.split('_')[1])
            await show_series_episodes(update, context, series_id)
            return
        
        elif data.startswith('ep_'):
            episode_id = int(data.split('_')[1])
            await show_episode_details(update, context, episode_id)
            return
        
    except Exception as e:
        logger.error(f"❌ خطأ في معالجة الزر {data}: {e}")
        error_msg = "❌ حدث خطأ في معالجة طلبك. يرجى المحاولة مرة أخرى."
        
        try:
            await query.edit_message_text(error_msg)
        except:
            await query.message.reply_text(error_msg)

async def show_series_episodes(update: Update, context: ContextTypes.DEFAULT_TYPE, series_id):
    """عرض حلقات مسلسل محدد"""
    query = update.callback_query
    
    try:
        with engine.connect() as conn:
            series_info = conn.execute(
                text("SELECT id, name FROM series WHERE id = :id"),
                {"id": series_id}
            ).fetchone()
    except Exception as e:
        await query.edit_message_text(f"❌ خطأ في جلب معلومات المسلسل: {e}")
        return
    
    if not series_info:
        await query.edit_message_text("❌ المسلسل غير موجود.")
        return
    
    series_name = series_info[1]
    episodes = await get_series_episodes(series_id)
    
    if not episodes:
        message_text = f"🎬 *{series_name}*\n\n📭 لا توجد حلقات حالياً."
        keyboard = [[InlineKeyboardButton("⬅️ رجوع للمسلسلات", callback_data="all_series")]]
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
    message_text = f"🎬 *{series_name}*\n\n"
    keyboard = []
    
    for season_num in sorted(seasons.keys()):
        message_text += f"📁 *الموسم {season_num}:*\n"
        
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
        InlineKeyboardButton("⬅️ رجوع للمسلسلات", callback_data="all_series"),
        InlineKeyboardButton("🏠 الرئيسية", callback_data="home")
    ])
    
    await query.edit_message_text(
        message_text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_episode_details(update: Update, context: ContextTypes.DEFAULT_TYPE, episode_id):
    """عرض تفاصيل حلقة مع روابط"""
    query = update.callback_query
    
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT e.season, e.episode_number, e.telegram_message_id,
                       s.name as series_name, e.series_id
                FROM episodes e
                JOIN series s ON e.series_id = s.id
                WHERE e.id = :episode_id
            """), {"episode_id": episode_id}).fetchone()
    except Exception as e:
        await query.edit_message_text(f"❌ خطأ في جلب معلومات الحلقة: {e}")
        return
    
    if not result:
        await query.edit_message_text("❌ الحلقة غير موجودة.")
        return
    
    season, episode_num, msg_id, series_name, series_id = result
    
    # بناء الرابط
    if msg_id:
        episode_link = f"https://t.me/ShoofFilm/{msg_id}"
        link_text = f"🔗 [رابط الحلقة في القناة]({episode_link})"
    else:
        episode_link = None
        link_text = "⚠️ تعذر إنشاء رابط للحلقة."
    
    message_text = (
        f"🎬 *{series_name}*\n"
        f"📁 الموسم {season} - الحلقة {episode_num}\n\n"
        f"{link_text}\n\n"
        f"*ملاحظة:* تأكد من أنك منضم للقناة لمشاهدة الحلقة."
    )
    
    # بناء لوحة المفاتيح
    keyboard = []
    if episode_link:
        keyboard.append([InlineKeyboardButton("▶️ مشاهدة الحلقة", url=episode_link)])
    
    keyboard.append([
        InlineKeyboardButton("⬅️ رجوع للمسلسل", callback_data=f"series_{series_id}"),
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
    application.add_handler(CommandHandler("series", lambda u, c: show_series(u, c, sort_by="id_asc")))
    application.add_handler(CommandHandler("debug", debug_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # تشغيل البوت
    print("🤖 البوت يعمل باستخدام Polling...")
    print("📊 المسلسلات مرتبة حسب ID تصاعدياً (الأقدم أولاً)")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
