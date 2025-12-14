import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes
)
from sqlalchemy import create_engine, text
from datetime import datetime

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
async def check_table_exists():
    """التحقق من وجود الجداول المطلوبة"""
    if not engine:
        return False
    
    try:
        with engine.connect() as conn:
            # التحقق من وجود جدول series
            series_exists = conn.execute(text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'series'
                )
            """)).fetchone()[0]
            
            # التحقق من وجود جدول episodes
            episodes_exists = conn.execute(text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'episodes'
                )
            """)).fetchone()[0]
            
            return series_exists and episodes_exists
    except Exception as e:
        print(f"❌ خطأ في التحقق من الجداول: {e}")
        return False

async def get_all_series():
    """جلب جميع المسلسلات من قاعدة البيانات مرتبة حسب الأحدث"""
    if not engine:
        print("❌ محرك قاعدة البيانات غير متاح")
        return []
    
    # التحقق أولاً من وجود الجداول
    if not await check_table_exists():
        print("❌ الجداول غير موجودة في قاعدة البيانات")
        return []
    
    try:
        with engine.connect() as conn:
            # محاولة استخدام created_at إذا كان موجوداً
            try:
                # فحص إذا كان حقل created_at موجود في جدول series
                has_created_at = conn.execute(text("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name = 'series' AND column_name = 'created_at'
                """)).fetchone()
                
                if has_created_at:
                    # استخدام created_at للترتيب إذا كان موجوداً
                    print("✅ حقل created_at موجود، سيتم الترتيب حسب التاريخ")
                    result = conn.execute(text("""
                        SELECT s.id, s.name, COUNT(e.id) as episode_count
                        FROM series s
                        LEFT JOIN episodes e ON s.id = e.series_id
                        GROUP BY s.id, s.name, s.created_at
                        ORDER BY s.created_at DESC
                    """))
                else:
                    # استخدام id للترتيب (الأعلى = الأحدث)
                    print("⚠️ حقل created_at غير موجود، سيتم الترتيب حسب الـ ID")
                    result = conn.execute(text("""
                        SELECT s.id, s.name, COUNT(e.id) as episode_count
                        FROM series s
                        LEFT JOIN episodes e ON s.id = e.series_id
                        GROUP BY s.id, s.name
                        ORDER BY s.id DESC
                    """))
                
                series = result.fetchall()
                print(f"✅ تم جلب {len(series)} مسلسل من قاعدة البيانات")
                for s in series:
                    print(f"  - {s[1]} (ID: {s[0]}, حلقات: {s[2]})")
                return series
                
            except Exception as query_error:
                print(f"❌ خطأ في استعلام المسلسلات: {query_error}")
                # محاولة استعلام أبسط
                try:
                    result = conn.execute(text("SELECT id, name FROM series ORDER BY id DESC"))
                    series = result.fetchall()
                    print(f"✅ تم جلب {len(series)} مسلسل (استعلام مبسط)")
                    return [(s[0], s[1], 0) for s in series]  # إضافة عدد الحلقات كـ 0
                except Exception as simple_error:
                    print(f"❌ فشل الاستعلام المبسط: {simple_error}")
                    return []
                    
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

async def get_series_info(series_id):
    """جلب معلومات مسلسل محدد"""
    if not engine:
        return None
    
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT id, name FROM series WHERE id = :id
            """), {"id": series_id})
            return result.fetchone()
    except Exception as e:
        print(f"❌ خطأ في جلب معلومات المسلسل {series_id}: {e}")
        return None

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
/test_db - اختبار قاعدة البيانات
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

async def show_series(update: Update, context: ContextTypes.DEFAULT_TYPE, sort_by="date"):
    """عرض جميع المسلسلات (الأحدث أولاً)"""
    # التحقق من اتصال قاعدة البيانات
    if not engine:
        error_msg = "❌ قاعدة البيانات غير متاحة حالياً.\n\nيرجى التحقق من إعدادات قاعدة البيانات."
        if update.callback_query:
            await update.callback_query.edit_message_text(error_msg)
        else:
            await update.message.reply_text(error_msg)
        return
    
    print(f"🔍 طلب عرض المسلسلات بطريقة: {sort_by}")
    
    # التحقق من وجود الجداول
    if not await check_table_exists():
        error_msg = "❌ الجداول غير موجودة في قاعدة البيانات.\n\nقد تحتاج إلى تهيئة قاعدة البيانات أولاً."
        if update.callback_query:
            await update.callback_query.edit_message_text(error_msg)
        else:
            await update.message.reply_text(error_msg)
        return
    
    # تحديد طريقة الترتيب
    if sort_by == "alphabetical":
        series_list = await get_series_alphabetical()
        title = "📺 *قائمة المسلسلات (أبجدي)*\n\n"
    else:
        series_list = await get_all_series()
        title = "📺 *قائمة المسلسلات (الأحدث أولاً)*\n\n"
    
    print(f"📊 عدد المسلسلات المستلمة: {len(series_list)}")
    
    if not series_list:
        no_data_msg = "📭 لا توجد مسلسلات حالياً في قاعدة البيانات.\n\nيمكنك إضافة مسلسلات من لوحة التحكم."
        
        keyboard = [[InlineKeyboardButton("🔄 تحديث", callback_data="all_series")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await update.callback_query.edit_message_text(no_data_msg, reply_markup=reply_markup)
        else:
            await update.message.reply_text(no_data_msg, reply_markup=reply_markup)
        return
    
    # بناء النص
    text = title
    keyboard = []
    
    for series in series_list:
        if len(series) >= 3:
            series_id, name, episode_count = series[0], series[1], series[2]
        elif len(series) >= 2:
            series_id, name, episode_count = series[0], series[1], 0
        else:
            continue  # تخطي إذا لم تكن البيانات كافية
        
        # عرض اسم المسلسل بالكامل
        text += f"• {name}"
        if episode_count > 0:
            text += f" ({episode_count} حلقة)"
        text += "\n"
        
        # إنشاء زر المسلسل
        button_text = f"{name}"
        # إذا كان الاسم طويلاً جداً، نقوم بتقليمه
        if len(button_text) > 30:
            button_text = button_text[:28] + "..."
        if episode_count > 0:
            button_text += f" ({episode_count})"
        
        keyboard.append([
            InlineKeyboardButton(
                button_text,
                callback_data=f"series_{series_id}"
            )
        ])
    
    # أزرار التنقل والترتيب
    if sort_by == "date":
        keyboard.append([
            InlineKeyboardButton("🔄 الأحدث", callback_data="sort_date"),
            InlineKeyboardButton("🔤 أبجدي", callback_data="sort_alphabetical")
        ])
    else:
        keyboard.append([
            InlineKeyboardButton("🔄 أبجدي", callback_data="sort_alphabetical"),
            InlineKeyboardButton("📅 الأحدث", callback_data="sort_date")
        ])
    
    keyboard.append([InlineKeyboardButton("🏠 الرئيسية", callback_data="home")])
    keyboard.append([InlineKeyboardButton("🔄 تحديث القائمة", callback_data="all_series")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # الإرسال حسب مصدر الطلب
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        except Exception as e:
            print(f"❌ خطأ في تعديل الرسالة: {e}")
            await update.callback_query.message.reply_text(
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

async def test_db_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر /test_db - اختبار قاعدة البيانات"""
    try:
        if not engine:
            await update.message.reply_text("❌ محرك قاعدة البيانات غير متصل.")
            return
        
        with engine.connect() as conn:
            # اختبار الاتصال
            conn.execute(text("SELECT 1"))
            
            # التحقق من الجداول
            tables_result = conn.execute(text("""
                SELECT table_name FROM information_schema.tables 
                WHERE table_schema = 'public'
            """)).fetchall()
            
            tables = [row[0] for row in tables_result]
            
            # التحقق من محتوى جدول series
            series_count = 0
            sample_series = []
            if 'series' in tables:
                series_count_result = conn.execute(text("SELECT COUNT(*) FROM series")).fetchone()
                series_count = series_count_result[0] if series_count_result else 0
                
                if series_count > 0:
                    sample_result = conn.execute(text("SELECT id, name FROM series LIMIT 3")).fetchall()
                    sample_series = [f"{row[1]} (ID: {row[0]})" for row in sample_result]
            
            # التحقق من محتوى جدول episodes
            episodes_count = 0
            if 'episodes' in tables:
                episodes_count_result = conn.execute(text("SELECT COUNT(*) FROM episodes")).fetchone()
                episodes_count = episodes_count_result[0] if episodes_count_result else 0
            
            reply_text = (
                f"📊 **نتيجة اختبار قاعدة البيانات:**\n\n"
                f"• الاتصال: ✅ ناجح\n"
                f"• الجداول الموجودة: {', '.join(tables) if tables else 'لا توجد جداول'}\n"
                f"• عدد المسلسلات: {series_count}\n"
                f"• عدد الحلقات: {episodes_count}\n"
            )
            
            if sample_series:
                reply_text += f"• أمثلة على المسلسلات:\n  - " + "\n  - ".join(sample_series)
            
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
            # إحصائيات المسلسلات
            series_result = conn.execute(text("SELECT COUNT(*) FROM series")).fetchone()
            episodes_result = conn.execute(text("SELECT COUNT(*) FROM episodes")).fetchone()
            sample_result = conn.execute(text("SELECT name FROM series LIMIT 5")).fetchall()
            
            # آخر مسلسلات مضافة
            recent_series = conn.execute(text("""
                SELECT name FROM series 
                ORDER BY id DESC 
                LIMIT 3
            """)).fetchall()
        
        series_count = series_result[0] if series_result else 0
        episodes_count = episodes_result[0] if episodes_result else 0
        sample_names = [row[0] for row in sample_result] if sample_result else ["لا يوجد"]
        recent_names = [row[0] for row in recent_series] if recent_series else ["لا يوجد"]
        
        reply_text = (
            f"📊 **فحص النظام:**\n"
            f"• قاعدة البيانات: {'✅ متصلة' if engine else '❌ غير متصلة'}\n"
            f"• عدد المسلسلات: `{series_count}`\n"
            f"• عدد الحلقات: `{episodes_count}`\n"
            f"• أمثلة على المسلسلات: {', '.join(sample_names)}\n"
            f"• آخر المسلسلات المضافة: {', '.join(recent_names)}"
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
    await query.answer()
    
    data = query.data
    
    print(f"🔘 زر مضغوط: {data}")
    
    if data == 'home':
        await start(update, context)
        return
    
    elif data == 'all_series':
        await show_series(update, context, sort_by="date")
        return
    
    elif data == 'sort_date':
        await show_series(update, context, sort_by="date")
        return
    
    elif data == 'sort_alphabetical':
        await show_series(update, context, sort_by="alphabetical")
        return
    
    elif data.startswith('series_'):
        try:
            series_id = int(data.split('_')[1])
            await show_series_episodes(update, context, series_id)
        except ValueError:
            await query.edit_message_text("❌ معرّف المسلسل غير صحيح.")
        return
    
    elif data.startswith('ep_'):
        try:
            episode_id = int(data.split('_')[1])
            await show_episode_details(update, context, episode_id)
        except ValueError:
            await query.edit_message_text("❌ معرّف الحلقة غير صحيح.")
        return
    
    elif data == 'back_to_series':
        await show_series(update, context, sort_by="date")
        return

async def show_series_episodes(update: Update, context: ContextTypes.DEFAULT_TYPE, series_id):
    """عرض حلقات مسلسل محدد"""
    query = update.callback_query
    
    # جلب معلومات المسلسل
    series_info = await get_series_info(series_id)
    
    if not series_info:
        await query.edit_message_text("❌ المسلسل غير موجود أو تم حذفه.")
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
    application.add_handler(CommandHandler("series", lambda u, c: show_series(u, c, sort_by="date")))
    application.add_handler(CommandHandler("debug", debug_command))
    application.add_handler(CommandHandler("test_db", test_db_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # تشغيل البوت
    print("🤖 البوت يعمل باستخدام Polling...")
    print(f"📊 حالة قاعدة البيانات: {'✅ متصلة' if engine else '❌ غير متصلة'}")
    
    if engine:
        print("🔍 جاري اختبار قاعدة البيانات...")
        try:
            with engine.connect() as conn:
                # اختبار بسيط للجداول
                result = conn.execute(text("SELECT COUNT(*) FROM series")).fetchone()
                print(f"📊 عدد المسلسلات في قاعدة البيانات: {result[0]}")
        except Exception as e:
            print(f"⚠️ تحذير: {e}")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
