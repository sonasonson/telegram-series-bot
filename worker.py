import os
import asyncio
import re
import sys
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import Message
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
                name VARCHAR(255) UNIQUE NOT NULL,
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
    print("✅ تم التحقق من هياكل الجداول.")
except Exception as e:
    print(f"⚠️ ملاحظة حول الجداول: {e}")

# ==============================
# 4. دوال المساعدة (التحليل والحفظ)
# ==============================
def parse_series_info(message_text):
    """تحليل نص الرسالة لاستخراج اسم المسلسل ورقم الحلقة."""
    if not message_text:
        return None, None
    
    text_cleaned = message_text.strip()
    
    pattern1 = r"^(.*?[^\d])\s+(\d+)$"
    pattern2 = r"^(.*?)\s+الحلقة\s+(\d+)$"
    
    match = re.search(pattern1, text_cleaned)
    if match:
        series_name = match.group(1).strip()
        episode_num = int(match.group(2))
        return series_name, episode_num
    
    match = re.search(pattern2, text_cleaned)
    if match:
        series_name = match.group(1).strip()
        episode_num = int(match.group(2))
        return series_name, episode_num
    
    return None, None

def save_to_database(series_name, episode_num, telegram_msg_id, series_id=None):
    """حفظ المسلسل والحلقة في قاعدة البيانات مع series_id"""
    try:
        with engine.begin() as conn:
            # إذا لم يتم تمرير series_id، ابحث عنه
            if not series_id:
                result = conn.execute(
                    text("SELECT id FROM series WHERE name = :name"),
                    {"name": series_name}
                ).fetchone()
                
                if not result:
                    # إضافة مسلسل جديد
                    conn.execute(
                        text("INSERT INTO series (name) VALUES (:name)"),
                        {"name": series_name}
                    )
                    # جلب الـ ID الجديد
                    result = conn.execute(
                        text("SELECT id FROM series WHERE name = :name"),
                        {"name": series_name}
                    ).fetchone()
                
                series_id = result[0]
            
            # إضافة الحلقة مع series_id و channel_id الثابت
            conn.execute(
                text("""
                    INSERT INTO episodes (series_id, season, episode_number, 
                           telegram_message_id, telegram_channel_id)
                    VALUES (:sid, 1, :ep_num, :msg_id, :channel)
                    ON CONFLICT (telegram_message_id) DO NOTHING
                """),
                {
                    "sid": series_id,
                    "ep_num": episode_num,
                    "msg_id": telegram_msg_id,
                    "channel": "@ShoofFilm"  # <-- استخدم المعرف الثابت هنا
                }
            )
            
        print(f"✅ تمت إضافة/تحديث: {series_name} (ID:{series_id}) - الحلقة {episode_num}")
        return True
        
    except SQLAlchemyError as e:
        print(f"❌ خطأ في قاعدة البيانات: {e}")
        return False

# ==============================
# 5. الدالة الجديدة: استيراد المسلسلات القديمة
# ==============================
async def import_channel_history(client, channel):
    """استيراد جميع الرسائل القديمة من القناة."""
    print("\n" + "="*50)
    print("📂 بدء استيراد المسلسلات القديمة من القناة...")
    print("="*50)
    
    imported_count = 0
    skipped_count = 0
    
    try:
        # جلب جميع الرسائل (يمكنك تعديل الحد إذا كانت القناة كبيرة)
        async for message in client.iter_messages(channel, limit=1000):
            if not message.text:
                continue
            
            series_name, episode_num = parse_series_info(message.text)
            if series_name and episode_num:
                if save_to_database(series_name, episode_num, message.id):
                    imported_count += 1
                else:
                    skipped_count += 1
        
        print("="*50)
        print(f"✅ اكتمل الاستيراد!")
        print(f"   - تم استيراد: {imported_count} حلقة جديدة")
        print(f"   - تم تخطي: {skipped_count} حلقة (موجودة مسبقاً)")
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
        
        # استيراد المسلسلات القديمة إذا كان مفعلاً
        if IMPORT_HISTORY:
            await import_channel_history(client, channel)
        else:
            print("⚠️ استيراد المسلسلات القديمة معطل. لتفعيله، أضف IMPORT_HISTORY=true في متغيرات البيئة.")
        
        # مراقبة الرسائل الجديدة
        @client.on(events.NewMessage(chats=channel))
        async def handler(event):
            message = event.message
            if message.text:
                print(f"📥 رسالة جديدة: {message.text[:50]}...")
                series_name, episode_num = parse_series_info(message.text)
                if series_name and episode_num:
                    print(f"   تم التعرف على: {series_name} - الحلقة {episode_num}")
                    save_to_database(series_name, episode_num, message.id)
        
        print("\n🎯 جاهز لاستقبال المسلسلات الجديدة من القناة...")
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
    print("🚀 بدء تشغيل Worker لمراقبة قناة المسلسلات...")
    asyncio.run(monitor_channel())
