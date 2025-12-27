import os
import asyncio
import re
import sys
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import Message, Channel
from telethon.tl.functions.channels import GetFullChannelRequest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

# ==============================
# 1. إعدادات التهيئة من متغيرات البيئة
# ==============================
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "https://t.me/ShoofFilm")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
STRING_SESSION = os.environ.get("STRING_SESSION", "")
IMPORT_HISTORY = os.environ.get("IMPORT_HISTORY", "false").lower() == "true"  # تفعيل/تعطيل الاستيراد
CHECK_DELETED_MESSAGES = os.environ.get("CHECK_DELETED_MESSAGES", "true").lower() == "true"  # تفعيل/تعطيل التحقق من المحذوفات

# تحقق من وجود المتغيرات الأساسية
if not all([API_ID, API_HASH, DATABASE_URL, STRING_SESSION]):
    print("❌ خطأ: واحد أو أكثر من المتغيرات التالية مفقود: API_ID, API_HASH, DATABASE_URL, STRING_SESSION")
    sys.exit(1)

# إصلاح رابط قاعدة البيانات
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ==============================
# 2. إعداد الاتصال بقاعدة البيانات
# ==============================
try:
    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    print("✅ تم الاتصال بقاعدة البيانات بنجاح.")
except Exception as e:
    print(f"❌ فشل الاتصال بقاعدة البيانات: {e}")
    sys.exit(1)

# ==============================
# 3. إنشاء الجداول إذا لم تكن موجودة
# ==============================
try:
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS series (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                type VARCHAR(10) DEFAULT 'series',  -- 'series' أو 'movie'
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS episodes (
                id SERIAL PRIMARY KEY,
                series_id INTEGER REFERENCES series(id),
                season INTEGER DEFAULT 1,
                episode_number INTEGER NOT NULL,
                telegram_message_id INTEGER UNIQUE NOT NULL,
                telegram_channel_id VARCHAR(255),
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        # إنشاء فهرس لتسريع البحث
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_series_name_type ON series(name, type)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_episodes_telegram_msg_id ON episodes(telegram_message_id)"))
    print("✅ تم التحقق من هياكل الجداول.")
except Exception as e:
    print(f"⚠️ ملاحظة حول الجداول: {e}")

# ==============================
# 4. دوال المساعدة (التحليل والحفظ والحذف)
# ==============================
def clean_name(name):
    """تنظيف الاسم من كلمات 'مسلسل' و'فيلم' والأرقام في النهاية."""
    if not name:
        return name
    
    # إزالة كلمات "مسلسل" و"فيلم" من البداية
    name = re.sub(r'^(مسلسل\s+|فيلم\s+)', '', name, flags=re.IGNORECASE)
    
    # إزالة كلمات "مسلسل" و"فيلم" من أي مكان (إذا كانت منفصلة)
    name = re.sub(r'\s+(مسلسل|فيلم)\s+', ' ', name, flags=re.IGNORECASE)
    
    # تنظيف المسافات الزائدة
    name = re.sub(r'\s+', ' ', name).strip()
    
    return name

def extract_numbers_from_name(name):
    """استخراج الأرقام من الاسم (مثل 13 من 'يوم-13')"""
    # البحث عن نمط رقم في النهاية مع أو بدون شرطة
    match = re.search(r'[-_]?(\d+)$', name)
    if match:
        return int(match.group(1))
    return None

def parse_content_info(message_text):
    """تحليل نص الرسالة لاستخراج المعلومات."""
    if not message_text:
        return None, None, None, None
    
    text_cleaned = message_text.strip()
    
    # =============================================
    # 1. البحث عن نمط الأفلام: "فيلم يوم-13" أو "فيلم يوم 13"
    # =============================================
    # نمط 1: "فيلم يوم-13" أو "فيلم يوم_13"
    film_pattern_dash = r'^فيلم\s+(.+?)[-_](\d+)$'
    match = re.search(film_pattern_dash, text_cleaned, re.IGNORECASE)
    if match:
        content_type = 'movie'
        raw_name = match.group(1).strip()
        season_num = int(match.group(2))  # الرقم بعد الشرطة يعتبر موسم
        episode_num = 1  # الأفليس ليس لها حلقات
        clean_name_text = clean_name(raw_name)
        return clean_name_text, content_type, season_num, episode_num
    
    # نمط 2: "فيلم يوم 13"
    film_pattern_space = r'^فيلم\s+(.+?)\s+(\d+)$'
    match = re.search(film_pattern_space, text_cleaned, re.IGNORECASE)
    if match:
        content_type = 'movie'
        raw_name = match.group(1).strip()
        season_num = int(match.group(2))  # الرقم بعد المسافة يعتبر موسم
        episode_num = 1
        clean_name_text = clean_name(raw_name)
        return clean_name_text, content_type, season_num, episode_num
    
    # نمط 3: "فيلم [اسم]" بدون رقم
    film_pattern_name_only = r'^فيلم\s+(.+)$'
    match = re.search(film_pattern_name_only, text_cleaned, re.IGNORECASE)
    if match:
        content_type = 'movie'
        raw_name = match.group(1).strip()
        # محاولة استخراج رقم من الاسم نفسه (مثل "يوم-13")
        extracted_num = extract_numbers_from_name(raw_name)
        if extracted_num:
            # إزالة الرقم من الاسم
            raw_name = re.sub(r'[-_]?\d+$', '', raw_name).strip()
            season_num = extracted_num
        else:
            season_num = 1  # موسم افتراضي
        episode_num = 1
        clean_name_text = clean_name(raw_name)
        return clean_name_text, content_type, season_num, episode_num
    
    # =============================================
    # 2. البحث عن نمط المسلسل مع الموسم: "المحافظ الموسم 1 الحلقة 1"
    # =============================================
    series_season_pattern = r'^(.*?)\s+الموسم\s+(\d+)\s+الحلقة\s+(\d+)$'
    match = re.search(series_season_pattern, text_cleaned)
    if match:
        content_type = 'series'
        raw_name = match.group(1).strip()
        season_num = int(match.group(2))
        episode_num = int(match.group(3))
        clean_name_text = clean_name(raw_name)
        return clean_name_text, content_type, season_num, episode_num
    
    # =============================================
    # 3. البحث عن نمط المسلسل بدون موسم: "المحافظ الحلقة 1"
    # =============================================
    series_episode_pattern = r'^(.*?)\s+الحلقة\s+(\d+)$'
    match = re.search(series_episode_pattern, text_cleaned)
    if match:
        content_type = 'series'
        raw_name = match.group(1).strip()
        season_num = 1  # موسم افتراضي
        episode_num = int(match.group(2))
        clean_name_text = clean_name(raw_name)
        return clean_name_text, content_type, season_num, episode_num
    
    # =============================================
    # 4. البحث عن نمط بسيط: "المحافظ 1"
    # =============================================
    simple_pattern = r'^(.*?[^\d\s])\s+(\d+)$'
    match = re.search(simple_pattern, text_cleaned)
    if match:
        # محاولة التمييز بين المسلسل والفيلم
        raw_name = match.group(1).strip()
        
        # إذا كان الاسم يحتوي على "فيلم" فهو فيلم
        if 'فيلم' in raw_name.lower():
            content_type = 'movie'
            season_num = int(match.group(2))  # الرقم يعتبر موسم
            episode_num = 1
        else:
            content_type = 'series'
            season_num = 1  # موسم افتراضي
            episode_num = int(match.group(2))  # الرقم يعتبر حلقة
        
        clean_name_text = clean_name(raw_name)
        return clean_name_text, content_type, season_num, episode_num
    
    # =============================================
    # 5. نمط المسلسل العربي: "مسلسل المحافظ الموسم 1 الحلقة 1"
    # =============================================
    arabic_series_pattern = r'^مسلسل\s+(.*?)\s+الموسم\s+(\d+)\s+الحلقة\s+(\d+)$'
    match = re.search(arabic_series_pattern, text_cleaned, re.IGNORECASE)
    if match:
        content_type = 'series'
        raw_name = match.group(1).strip()
        season_num = int(match.group(2))
        episode_num = int(match.group(3))
        clean_name_text = clean_name(raw_name)
        return clean_name_text, content_type, season_num, episode_num
    
    # =============================================
    # 6. نمط المسلسل العربي بدون موسم: "مسلسل المحافظ الحلقة 1"
    # =============================================
    arabic_series_simple = r'^مسلسل\s+(.*?)\s+الحلقة\s+(\d+)$'
    match = re.search(arabic_series_simple, text_cleaned, re.IGNORECASE)
    if match:
        content_type = 'series'
        raw_name = match.group(1).strip()
        season_num = 1
        episode_num = int(match.group(2))
        clean_name_text = clean_name(raw_name)
        return clean_name_text, content_type, season_num, episode_num
    
    # إذا لم يتطابق مع أي نمط
    print(f"⚠️ لم يتم التعرف على النمط للنص: {text_cleaned}")
    
    # محاولة أخيرة: إذا كان النص يحتوي على "فيلم" في البداية
    if text_cleaned.lower().startswith('فيلم'):
        content_type = 'movie'
        raw_name = text_cleaned[4:].strip()  # إزالة "فيلم"
        extracted_num = extract_numbers_from_name(raw_name)
        if extracted_num:
            raw_name = re.sub(r'[-_]?\d+$', '', raw_name).strip()
            season_num = extracted_num
        else:
            season_num = 1
        episode_num = 1
        clean_name_text = clean_name(raw_name)
        print(f"   ⚠️ معالجة كفيلم افتراضي: {clean_name_text}")
        return clean_name_text, content_type, season_num, episode_num
    
    return None, None, None, None

def save_to_database(name, content_type, season_num, episode_num, telegram_msg_id, series_id=None):
    """حفظ المحتوى في قاعدة البيانات."""
    try:
        with engine.begin() as conn:
            # البحث عن المسلسل/الفيلم بنفس الاسم والنوع
            if not series_id:
                result = conn.execute(
                    text("""
                        SELECT id FROM series 
                        WHERE name = :name AND type = :type
                    """),
                    {"name": name, "type": content_type}
                ).fetchone()
                
                if not result:
                    # إضافة مسلسل/فيلم جديد
                    conn.execute(
                        text("""
                            INSERT INTO series (name, type) 
                            VALUES (:name, :type)
                        """),
                        {"name": name, "type": content_type}
                    )
                    # جلب الـ ID الجديد
                    result = conn.execute(
                        text("""
                            SELECT id FROM series 
                            WHERE name = :name AND type = :type
                        """),
                        {"name": name, "type": content_type}
                    ).fetchone()
                
                series_id = result[0]
            
            # إضافة الحلقة/الجزء
            conn.execute(
                text("""
                    INSERT INTO episodes (series_id, season, episode_number, 
                           telegram_message_id, telegram_channel_id)
                    VALUES (:sid, :season, :ep_num, :msg_id, :channel)
                    ON CONFLICT (telegram_message_id) DO NOTHING
                """),
                {
                    "sid": series_id,
                    "season": season_num,
                    "ep_num": episode_num,
                    "msg_id": telegram_msg_id,
                    "channel": "@ShoofFilm"
                }
            )
            
        type_arabic = "مسلسل" if content_type == 'series' else "فيلم"
        if content_type == 'movie':
            print(f"✅ تمت إضافة {type_arabic}: {name} - الجزء {season_num}")
        else:
            print(f"✅ تمت إضافة {type_arabic}: {name} - الموسم {season_num} الحلقة {episode_num}")
        return True
        
    except SQLAlchemyError as e:
        print(f"❌ خطأ في قاعدة البيانات: {e}")
        return False

def delete_from_database(message_id):
    """حذف حلقة/جزء من قاعدة البيانات عند حذفها من القناة."""
    try:
        with engine.begin() as conn:
            # البحث عن الحلقة المراد حذفها
            episode_result = conn.execute(
                text("""
                    SELECT e.id, e.series_id, s.name, s.type, e.season, e.episode_number
                    FROM episodes e
                    JOIN series s ON e.series_id = s.id
                    WHERE e.telegram_message_id = :msg_id
                """),
                {"msg_id": message_id}
            ).fetchone()
            
            if not episode_result:
                print(f"⚠️ لم يتم العثور على الحلقة {message_id} في قاعدة البيانات")
                return False
            
            episode_id, series_id, name, content_type, season, episode_num = episode_result
            
            # حذف الحلقة
            conn.execute(
                text("DELETE FROM episodes WHERE id = :episode_id"),
                {"episode_id": episode_id}
            )
            
            # التحقق مما إذا كان المسلسل/الفيلم لا يزال لديه حلقات أخرى
            remaining_episodes = conn.execute(
                text("SELECT COUNT(*) FROM episodes WHERE series_id = :series_id"),
                {"series_id": series_id}
            ).scalar()
            
            type_arabic = "مسلسل" if content_type == 'series' else "فيلم"
            
            if remaining_episodes == 0:
                # إذا لم يعد هناك حلقات، حذف المسلسل/الفيلم أيضًا
                conn.execute(
                    text("DELETE FROM series WHERE id = :series_id"),
                    {"series_id": series_id}
                )
                print(f"🗑️ تم حذف {type_arabic}: {name} بالكامل (لا توجد حلقات/أجزاء متبقية)")
            else:
                if content_type == 'movie':
                    print(f"🗑️ تم حذف {type_arabic}: {name} - الجزء {season}")
                else:
                    print(f"🗑️ تم حذف {type_arabic}: {name} - الموسم {season} الحلقة {episode_num}")
            
            return True
            
    except SQLAlchemyError as e:
        print(f"❌ خطأ في حذف من قاعدة البيانات: {e}")
        return False

async def check_deleted_messages(client, channel, last_message_id):
    """التحقق من الرسائل المحذوفة في القناة."""
    print("\n🔍 التحقق من الرسائل المحذوفة...")
    
    try:
        with engine.connect() as conn:
            # جلب جميع معرفات الرسائل المخزنة في قاعدة البيانات
            stored_messages = conn.execute(
                text("SELECT telegram_message_id FROM episodes ORDER BY telegram_message_id")
            ).fetchall()
            
            stored_ids = [msg[0] for msg in stored_messages]
            
            if not stored_ids:
                print("   لا توجد رسائل مخزنة للتحقق")
                return
            
            # جلب معرفات الرسائل الحالية في القناة
            current_ids = []
            async for message in client.iter_messages(channel, limit=1000):
                current_ids.append(message.id)
            
            # تحديد الرسائل المحذوفة (الموجودة في قاعدة البيانات ولكن ليس في القناة)
            deleted_ids = []
            for stored_id in stored_ids:
                if stored_id not in current_ids:
                    deleted_ids.append(stored_id)
            
            if deleted_ids:
                print(f"   تم العثور على {len(deleted_ids)} رسالة محذوفة")
                for msg_id in deleted_ids:
                    print(f"   🗑️ معالجة الرسالة المحذوفة: {msg_id}")
                    delete_from_database(msg_id)
            else:
                print("   ✅ لا توجد رسائل محذوفة")
                
    except Exception as e:
        print(f"❌ خطأ في التحقق من الرسائل المحذوفة: {e}")

# ==============================
# 5. استيراد المسلسلات القديمة
# ==============================
async def import_channel_history(client, channel):
    """استيراد جميع الرسائل القديمة من القناة بأقدمها أولاً."""
    print("\n" + "="*50)
    print("📂 بدء استيراد المحتوى القديم من القناة...")
    print("="*50)
    
    imported_count = 0
    skipped_count = 0
    error_count = 0
    
    try:
        # جمع جميع الرسائل أولاً
        all_messages = []
        async for message in client.iter_messages(channel, limit=1000):
            all_messages.append(message)
        
        # عكس الترتيب للحصول على الأقدم أولاً
        all_messages.reverse()
        
        print(f"📊 تم جمع {len(all_messages)} رسالة للاستيراد...")
        
        for message in all_messages:
            if not message.text:
                continue
            
            try:
                name, content_type, season_num, episode_num = parse_content_info(message.text)
                if name and content_type and episode_num:
                    if save_to_database(name, content_type, season_num, episode_num, message.id):
                        imported_count += 1
                    else:
                        skipped_count += 1
                else:
                    print(f"⚠️ لم يتم تحليل الرسالة: {message.text[:50]}...")
                    error_count += 1
            except Exception as e:
                print(f"❌ خطأ في معالجة الرسالة {message.id}: {e}")
                error_count += 1
        
        print("="*50)
        print(f"✅ اكتمل الاستيراد!")
        print(f"   - تم استيراد: {imported_count} عنصر جديد")
        print(f"   - تم تخطي: {skipped_count} عنصر (موجود مسبقاً)")
        print(f"   - فشل تحليل: {error_count} رسالة")
        print("="*50)
        
    except Exception as e:
        print(f"❌ خطأ أثناء استيراد التاريخ: {e}")

# ==============================
# 6. الدالة الرئيسية لمراقبة القناة
# ==============================
async def monitor_channel():
    """الدالة الرئيسية لمراقبة القناة وإضافة المحتوى."""
    print("="*50)
    print(f"🔍 بدء مراقبة القناة: {CHANNEL_USERNAME}")
    print("="*50)
    
    client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)
    
    try:
        await client.start()
        print("✅ تم الاتصال بـ Telegram بنجاح.")
        
        channel = await client.get_entity(CHANNEL_USERNAME)
        print(f"✅ تم العثور على القناة: {channel.title}")
        
        # الحصول على آخر رسالة في القناة
        last_message = await client.get_messages(channel, limit=1)
        last_message_id = last_message[0].id if last_message else 0
        
        # استيراد المحتوى القديم إذا كان مفعلاً
        if IMPORT_HISTORY:
            await import_channel_history(client, channel)
        else:
            print("⚠️ استيراد المحتوى القديم معطل. لتفعيله، أضف IMPORT_HISTORY=true في متغيرات البيئة.")
        
        # التحقق من الرسائل المحذوفة إذا كان مفعلاً
        if CHECK_DELETED_MESSAGES:
            await check_deleted_messages(client, channel, last_message_id)
        
        # مراقبة الرسائل الجديدة
        @client.on(events.NewMessage(chats=channel))
        async def handler(event):
            message = event.message
            if message.text:
                print(f"📥 رسالة جديدة: {message.text[:50]}...")
                name, content_type, season_num, episode_num = parse_content_info(message.text)
                if name and content_type and episode_num:
                    type_arabic = "مسلسل" if content_type == 'series' else "فيلم"
                    if content_type == 'movie':
                        print(f"   تم التعرف على {type_arabic}: {name} - الجزء {season_num}")
                    else:
                        print(f"   تم التعرف على {type_arabic}: {name} - الموسم {season_num} الحلقة {episode_num}")
                    save_to_database(name, content_type, season_num, episode_num, message.id)
        
        # مراقبة حذف الرسائل
        @client.on(events.MessageDeleted(chats=channel))
        async def handler(event):
            deleted_ids = event.deleted_ids
            for msg_id in deleted_ids:
                print(f"🗑️ تم حذف رسالة من القناة: {msg_id}")
                delete_from_database(msg_id)
        
        print("\n🎯 جاهز لاستقبال المحتوى الجديد ومراقبة الحذف من القناة...")
        print("   (اضغط Ctrl+C في Railway لإيقاف المراقبة)\n")
        
        await client.run_until_disconnected()
        
    except Exception as e:
        print(f"❌ خطأ في تشغيل الـ Worker: {e}")
    finally:
        await client.disconnect()
        print("🛑 تم إيقاف مراقبة القناة.")

# ==============================
# 7. نقطة دخول البرنامج
# ==============================
if __name__ == "__main__":
    print("🚀 بدء تشغيل Worker لمراقبة قناة المسلسلات والأفلام...")
    asyncio.run(monitor_channel())
