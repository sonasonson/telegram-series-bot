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
# تأكد من إضافة BOT_TOKEN في متغيرات البيئة على Railway (خدمة web)
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
async def get_all_series():
    """جلب جميع المسلسلات من قاعدة البيانات - الجديد في الأسفل"""
    if not engine:
        return []
    
    try:
        with engine.connect() as conn:
            # جلب المسلسلات مع عدد حلقات كل منها
            result = conn.execute(text("""
                SELECT s.id, s.name, 
                       COUNT(e.id) as episode_count,
                       COALESCE(s.created_at, s.added_at, NOW()) as order_date
                FROM series s
                LEFT JOIN episodes e ON s.id = e.series_id
                GROUP BY s.id, s.name, order_date
                ORDER BY MIN(COALESCE(s.created_at, s.added_at, e.added_at, NOW())) ASC
                -- الجديد في الأسفل
            """))
            return result.fetchall()
    except Exception as e:
        print(f"❌ خطأ في جلب المسلسلات: {e}")
        return []

async def get_series_episodes(series_id):
    """جلب حلقات مسلسل محدد"""
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
        print(f"❌ خطأ في جلب حلقات المسلسل {series_id}: {e}")
        return []

# ==============================
# 3. دوال البوت الرئيسية
# ==============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /start"""
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

async def show_series(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض جميع المسلسلات (يعمل مع /series ومع الزر)"""
    if not engine:
        error_msg = "❌ قاعدة البيانات غير متاحة حالياً."
        if update.callback_query:
            await update.callback_query.edit_message_text(error_msg)
        else:
            await update.message.reply_text(error_msg)
        return
    
    series_list = await get_all_series()
    
    if not series_list:
        no_data_msg = "📭 لا توجد مسلسلات حالياً."
        if update.callback_query:
            await update.callback_query.edit_message_text(no_data_msg)
        else:
            await update.message.reply_text(no_data_msg)
        return
    
    # بناء النص
    text = "📺 *قائمة المسلسلات*\n\n"
    keyboard = []
    
    for series in series_list:
        series_id, name, episode_count = series
        text += f"• {name} ({episode_count} حلقة)\n"
        keyboard.append([
            InlineKeyboardButton(
                f"📺 {name[:15]} ({episode_count})",
                callback_data=f"series_{series_id}"
            )
        ])
    
    # أزرار التنقل
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
            sample_result = conn.execute(text("SELECT name FROM series LIMIT 5")).fetchall()
            
            # آخر حلقات مضافة
            recent_eps = conn.execute(text("""
                SELECT s.name, e.episode_number 
                FROM episodes e 
                JOIN series s ON e.series_id = s.id 
                ORDER BY e.id DESC 
                LIMIT 3
            """)).fetchall()
        
        series_count = series_result[0] if series_result else 0
        episodes_count = episodes_result[0] if episodes_result else 0
        sample_names = [row[0] for row in sample_result] if sample_result else ["لا يوجد"]
        recent_episodes = [f"{row[0]} (ح{row[1]})" for row in recent_eps] if recent_eps else ["لا يوجد"]
        
        reply_text = (
            f"📊 **فحص النظام:**\n"
            f"• قاعدة البيانات: {'✅ متصلة' if engine else '❌ غير متصلة'}\n"
            f"• عدد المسلسلات: `{series_count}`\n"
            f"• عدد الحلقات: `{episodes_count}`\n"
            f"• أمثلة على المسلسلات: {', '.join(sample_names)}\n"
            f"• حلقات حديثة: {', '.join(recent_episodes)}"
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
    
    elif data == 'all_series':
        # هذا هو التصحيح المهم: استدعاء show_series
        await show_series(update, context)
        return
    
    elif data.startswith('series_'):
        series_id = int(data.split('_')[1])
        await show_series_episodes(update, context, series_id)
        return
    
    elif data.startswith('ep_'):
        episode_id = int(data.split('_')[1])
        await show_episode_details(update, context, episode_id)
        return
    
    elif data == 'back_to_series':
        await show_series(update, context)
        return

async def show_series_episodes(update: Update, context: ContextTypes.DEFAULT_TYPE, series_id):
    """عرض حلقات مسلسل محدد"""
    query = update.callback_query
    
    # جلب معلومات المسلسل
    try:
        with engine.connect() as conn:
            # استخدم دالة text() من sqlalchemy للاستعلام
            from sqlalchemy import text as sql_text
            series_info = conn.execute(
                sql_text("SELECT name FROM series WHERE id = :id"),
                {"id": series_id}
            ).fetchone()
    except Exception as e:
        await query.edit_message_text(f"❌ خطأ في جلب معلومات المسلسل: {e}")
        return
    
    if not series_info:
        await query.edit_message_text("❌ المسلسل غير موجود.")
        return
    
    series_name = series_info[0]
    episodes = await get_series_episodes(series_id)
    
    if not episodes:
        message_text = f"🎬 *{series_name}*\n\n📭 لا توجد حلقات حالياً."
        keyboard = [[InlineKeyboardButton("⬅️ رجوع", callback_data="all_series")]]
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
    
    # بناء النص - استخدم اسم متغير مختلف عن `text`
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
        InlineKeyboardButton("⬅️ رجوع", callback_data="all_series"),
        InlineKeyboardButton("🏠 الرئيسية", callback_data="home")
    ])
    
    await query.edit_message_text(
        message_text,  # استخدم المتغير الجديد message_text
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_episode_details(update: Update, context: ContextTypes.DEFAULT_TYPE, episode_id):
    """عرض تفاصيل حلقة مع روابط - معدل لاستخدام رابط الدعوة"""
    query = update.callback_query
    
    try:
        with engine.connect() as conn:
            from sqlalchemy import text as sql_text
            result = conn.execute(sql_text("""
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
    
    # 🔧 بناء الرابط باستخدام رابط الدعوة الثابت
    if msg_id:
        # استخدم رابط الدعوة الخاص بك هنا مباشرة
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
    application.add_handler(CommandHandler("series", show_series))
    application.add_handler(CommandHandler("debug", debug_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # تشغيل البوت
    print("🤖 البوت يعمل باستخدام Polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
