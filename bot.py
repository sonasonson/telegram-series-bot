import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from config import Config
from sqlalchemy import create_engine, text

# إعداد التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# تأكد من وجود التوكن
if not Config.BOT_TOKEN:
    logger.error("❌ BOT_TOKEN غير موجود! قم بإضافته في متغيرات البيئة")
    exit(1)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /start"""
    keyboard = [
        [InlineKeyboardButton("📺 جميع المسلسلات", callback_data='all_series')],
        [InlineKeyboardButton("🔍 بحث سريع", switch_inline_query_current_chat='')],
        [InlineKeyboardButton("⭐ المفضلة", callback_data='favorites')],
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    user = update.effective_user
    welcome_text = f"""
🎬 *مرحباً {user.first_name}!* 🎬

*بوت فهرس المسلسلات* يمكنك من:
• تصفح جميع المسلسلات في القناة
• البحث عن أي مسلسل أو حلقة
• الوصول السريع للحلقات

📌 *الأوامر المتاحة:*
/start - عرض هذه الرسالة
/series - عرض جميع المسلسلات
/search - البحث عن مسلسل
    """
    
    await update.message.reply_text(
        welcome_text,
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def show_series(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة المسلسلات"""
    # هنا سيتم جلب المسلسلات من قاعدة البيانات
    series_list = [
        {"id": 1, "name": "مسلسل 1", "episodes": 10},
        {"id": 2, "name": "مسلسل 2", "episodes": 15},
    ]
    
    if not series_list:
        await update.message.reply_text("📭 لا توجد مسلسلات حالياً.")
        return
    
    text = "📺 *قائمة المسلسلات*\n\n"
    keyboard = []
    
    for series in series_list:
        text += f"• {series['name']} ({series['episodes']} حلقة)\n"
        keyboard.append([
            InlineKeyboardButton(
                f"📺 {series['name']}", 
                callback_data=f"series_{series['id']}"
            )
        ])
    
    keyboard.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="home")])
    
    await update.message.reply_text(
        text,
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أزرار الإنلاين"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == 'home':
        await start(query, context)
    elif data == 'all_series':
        await show_series(update, context)
    elif data.startswith('series_'):
        series_id = data.split('_')[1]
        await show_episodes(update, context, series_id)

async def show_episodes(update: Update, context: ContextTypes.DEFAULT_TYPE, series_id):
    """عرض حلقات مسلسل"""
    # هنا سيتم جلب الحلقات من قاعدة البيانات
    episodes = [
        {"id": 1, "number": 1, "title": "الحلقة الأولى"},
        {"id": 2, "number": 2, "title": "الحلقة الثانية"},
    ]
    
    text = f"🎬 *المسلسل {series_id}*\n\n"
    keyboard = []
    
    for episode in episodes:
        text += f"• الحلقة {episode['number']}: {episode['title']}\n"
        keyboard.append([
            InlineKeyboardButton(
                f"▶️ الحلقة {episode['number']}",
                callback_data=f"ep_{episode['id']}"
            )
        ])
    
    keyboard.append([
        InlineKeyboardButton("⬅️ رجوع", callback_data="all_series"),
        InlineKeyboardButton("🏠 الرئيسية", callback_data="home")
    ])
    
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

def main():
    """الدالة الرئيسية لتشغيل البوت"""
    # إنشاء التطبيق
    application = Application.builder().token(Config.BOT_TOKEN).build()
    
    # ----- 🔍 إضافة أمر التصحيح (DEBUG) هنا -----
    from telegram.ext import CommandHandler
    # تأكد من أن هذه الاستيرادات تتطابق مع مشروعك. الأكثر شيوعاً:
    # من database.py: from database import Session, Series, Episode
    # أو إذا كان لديك DatabaseManager: from database import DatabaseManager
    from database import Session, Series, Episode  # <-- استبدل هذا بالسطر الصحيح لمشروعك
    
        async def debug_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """أمر /debug - فحص قاعدة البيانات باستخدام SQL المباشر"""
        try:
            # استخدم اتصال SQL مباشر لتجنب مشاكل تعريفات النماذج (ORM)
            from sqlalchemy import create_engine, text
            # تأكد من أن DATABASE_URL موجود في Config أو البيئة
            db_url = Config.DATABASE_URL if hasattr(Config, 'DATABASE_URL') else os.environ.get('DATABASE_URL')
            if not db_url:
                await update.message.reply_text("❌ خطأ: لم يتم العثور على رابط قاعدة البيانات (DATABASE_URL).")
                return
            
            engine = create_engine(db_url)
            
            with engine.connect() as conn:
                # 1. عد المسلسلات
                series_result = conn.execute(text("SELECT COUNT(*) FROM series")).fetchone()
                # 2. عد الحلقات
                episodes_result = conn.execute(text("SELECT COUNT(*) FROM episodes")).fetchone()
                # 3. جلب عينة من أسماء المسلسلات
                sample_result = conn.execute(text("SELECT name FROM series ORDER BY id LIMIT 5")).fetchall()
                # 4. (اختياري) جلب عينة من الحلقات الأخيرة
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
            recent_episodes = [f"{row[0]} (ح {row[1]})" for row in recent_eps] if recent_eps else ["لا يوجد"]
            
            reply_text = (
                f"📊 **فحص قاعدة البيانات (SQL مباشر):**\n"
                f"• عدد المسلسلات: `{series_count}`\n"
                f"• عدد الحلقات: `{episodes_count}`\n"
                f"• أمثلة على المسلسلات: {', '.join(sample_names)}\n"
                f"• حلقات مضافة حديثاً: {', '.join(recent_episodes)}"
            )
            
            await update.message.reply_text(reply_text, parse_mode='Markdown')
            
        except Exception as e:
            await update.message.reply_text(f"❌ خطأ في فحص قاعدة البيانات:\n`{str(e)[:300]}`")

    
    # أضف Handler لأمر /debug
    application.add_handler(CommandHandler("debug", debug_db))
    # ----- انتهاء إضافة أمر التصحيح -----
    
    # إضافة handlers الأصلية
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("series", show_series))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # تشغيل البوت
    port = int(os.environ.get('PORT', 8443))
    webhook_url = os.environ.get('WEBHOOK_URL', '')
    
    if webhook_url:
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=Config.BOT_TOKEN,
            webhook_url=f"{webhook_url}/{Config.BOT_TOKEN}"
        )
    else:
        print("🤖 البوت يعمل باستخدام Polling...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
