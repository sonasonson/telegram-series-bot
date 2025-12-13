import os
import asyncio
import re
import sys
from datetime import datetime
from telethon import TelegramClient, events
from telethon.sessions import StringSession  # مهم للجلسة المخزنة
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

# ==============================
# 1. إعدادات التهيئة من متغيرات البيئة على Railway
# ==============================
# تأكد من إضافة هذه المتغيرات في إعدادات خدمة `worker`:
# - API_ID، API_HASH، CHANNEL_USERNAME، DATABASE_URL، STRING_SESSION
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "@ShoofFilm")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
STRING_SESSION = os.environ.get("STRING_SESSION", "1BJWap1sBuzzoygXcwqBFnfSaqH1L8GeX8Ity6M4sUTplD3coTr-zzUowaR2B39wYq1-YStXztOJ8nBUwu4miCg7MGubDp8A_mP2g547lsxqMQ9Ggdb43twpELGV0rYM611lDx1Zfze-X5DUD5mYWcfH9NrG3EFoV1rKfbPyf07nI_tC4XU_cgnMMEOZALlhCwz_DIYBJ2oraG80z98mchqeaIhnUkL5iYVyrNki3pR0J9GPDHW43JL2LyPeH6IAgCNdxjQpwZe2VIHG6x-ZeEJUlSkXmOGgwnoGft1OeSLp-JlocaYArMQ2ns-v2sUjVmfZXQt_aSed2FBfy-JgDUc-7e80afnY=")  # الجلسة المخزنة

# تحقق من وجود جميع المتغيرات الأساسية
if not all([API_ID, API_HASH, DATABASE_URL, STRING_SESSION]):
    print("❌ خطأ: واحد أو أكثر من المتغيرات التالية مفقود: API_ID, API_HASH, DATABASE_URL, STRING_SESSION")
    print("   تأكد من إضافتها في إعدادات خدمة 'worker' على Railway.")
    sys.exit(1)

# إصلاح رابط قاعدة البيانات ليتوافق مع sqlalchemy
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ==============================
# 2. إعداد الاتصال بقاعدة البيانات
# ==============================
try:
    engine = create_engine(DATABASE_URL)
    # اختبار الاتصال
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    print("✅ تم الاتصال بقاعدة البيانات بنجاح.")
except Exception as e:
    print(f"❌ فشل الاتصال بقاعدة البيانات: {e}")
    sys.exit(1)

# ==============================
# 3. إنشاء الجداول إذا لم تكن موجودة (للمرة الأولى)
# ==============================
try:
    with engine.begin() as conn:
        # جدول المسلسلات
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS series (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        # جدول الحلقات
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
    print("✅ تم التحقق من هياكل الجداول (أو إنشاؤها).")
except Exception as e:
    print(f"⚠️ ملاحظة حول الجداول: {e}")

# ==============================
# 4. دالة لتحليل عناوين المسلسلات (مخصصة لقناتك @ShoofFilm)
# ==============================
def parse_series_info(message_text):
    """
    تحليل نص الرسالة لاستخراج اسم المسلسل ورقم الحلقة.
    يدعم النمطين في قناتك:
        - "برغم القانون 25"
        - "كارثة طبيعية الحلقة 1"
    """
    if not message_text:
        return None, None
    
    text_cleaned = message_text.strip()
    
    # النمط 1: "اسم المسلسل رقم" مثل "برغم القانون 25"
    pattern1 = r"^(.*?[^\d])\s+(\d+)$"
    # النمط 2: "اسم المسلسل الحلقة رقم" مثل "كارثة طبيعية الحلقة 1"
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

# ==============================
# 5. دالة لحفظ المسلسل في قاعدة البيانات
# ==============================
def save_to_database(series_name, episode_num, telegram_msg_id):
    """حفظ المسلسل والحلقة في قاعدة البيانات"""
    try:
        with engine.begin() as conn:
            # 1. البحث عن المسلسل أو إضافته
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
            
            # 2. إضافة الحلقة (تجنب التكرار)
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
                    "channel": CHANNEL_USERNAME
                }
            )
            
        print(f"✅ تمت إضافة/تحديث: {series_name} - الحلقة {episode_num}")
        return True
        
    except SQLAlchemyError as e:
        print(f"❌ خطأ في قاعدة البيانات لحفظ {series_name}: {e}")
        return False
    except Exception as e:
        print(f"❌ خطأ غير متوقع: {e}")
        return False

# ==============================
# 6. الدالة الرئيسية لمراقبة القناة
# ==============================
async def monitor_channel():
    """الدالة الرئيسية لمراقبة القناة وإضافة المحتوى الجديد"""
    print("=" * 50)
    print(f"🔍 بدء مراقبة القناة: {CHANNEL_USERNAME}")
    print("=" * 50)
    
    # إنشاء عميل Telethon باستخدام الجلسة المخزنة
    client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)
    
    try:
        # لن يطلب رقم هاتف الآن!
        await client.start()
        print("✅ تم الاتصال بـ Telegram بنجاح باستخدام الجلسة المخزنة.")
        
        # الحصول على كيان القناة
        try:
            channel = await client.get_entity(CHANNEL_USERNAME)
            print(f"✅ تم العثور على القناة: {channel.title}")
        except Exception as e:
            print(f"❌ لا يمكن العثور على القناة {CHANNEL_USERNAME}: {e}")
            print("   تأكد من:")
            print("   1. أن القناة عامة (Public) أو أن لديك صلاحية الوصول")
            print("   2. صحة اسم المستخدم (مثال: @ShoofFilm)")
            return
        
        # مراقبة الرسائل الجديدة
        @client.on(events.NewMessage(chats=channel))
        async def handler(event):
            """معالج الرسائل الجديدة"""
            message = event.message
            if message.text:
                print(f"📥 رسالة جديدة: {message.text[:50]}...")
                
                # تحليل الرسالة
                series_name, episode_num = parse_series_info(message.text)
                
                if series_name and episode_num:
                    print(f"   تم التعرف على: {series_name} - الحلقة {episode_num}")
                    # حفظ في قاعدة البيانات
                    save_to_database(series_name, episode_num, message.id)
                else:
                    print(f"   ⚠️ لم يتطابق مع نمط المسلسل (تم تخطيها)")
        
        print("\n🎯 جاهز لاستقبال المسلسلات الجديدة من القناة...")
        print("   (سيعمل حتى يتم إيقافه يدوياً)\n")
        
        # استمر في التشغيل حتى يتم إيقافه
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
