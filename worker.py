# worker.py
import os, asyncio, re
from telethon import TelegramClient
from sqlalchemy import create_engine, text

# 🚨 **مهم: اربط المتغيرات بشكل صحيح**
# 1. تأكد من أن هذه المتغيرات موجودة في إعدادات خدمة `worker` على Railway:
#    API_ID, API_HASH, CHANNEL_USERNAME, DATABASE_URL
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
CHANNEL = os.environ.get("CHANNEL_USERNAME", "@ShoofFilm")
DB_URL = os.environ.get("DATABASE_URL")  # هذا المتغير مهم جدًا وسيوفره Railway

# 2. أنشئ محرك قاعدة البيانات - تأكد من أن DB_URL ليس فارغًا
if DB_URL and DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)
engine = create_engine(DB_URL) if DB_URL else None

async def main():
    print("🔍 بدء مراقبة القناة...")
    if not engine:
        print("❌ خطأ: لم يتم العثور على رابط قاعدة البيانات (DATABASE_URL).")
        return

    client = TelegramClient('session', API_ID, API_HASH)
    await client.start()
    print(f"✅ تم الاتصال بتليجرام. جارٍ مراقبة القناة: {CHANNEL}")

    channel = await client.get_entity(CHANNEL)
    last_msg_id = 0

    while True:
        try:
            messages = await client.get_messages(channel, limit=10, min_id=last_msg_id)
            for msg in messages:
                if msg.id > last_msg_id:
                    last_msg_id = msg.id
                    if msg.text:
                        # 📌 **أنت هنا: أضف منطق تحليل رسالة القناة**
                        # مثال: استخراج اسم المسلسل ورقم الحلقة من النص
                        series_name, season, ep_num = parse_message(msg.text)
                        if series_name:
                            save_to_db(series_name, season, ep_num, msg.id)
            await asyncio.sleep(30)  # انتظر 30 ثانية قبل الفحص التالي
        except Exception as e:
            print(f"⚠️ خطأ: {e}")
            await asyncio.sleep(60)

# ❗ **وظيفتك: أكمل الدالتين التاليتين حسب تنسيق منشورات قناتك**
def parse_message(text):
    """
    دالة لتحليل نص الرسالة من القناة واستخراج معلومات المسلسل.
    أنت من يعرف نمط منشورات قناتك. مثال لنمط "مسلسل - الموسم 1 - الحلقة 5":
    """
    # مثال بسيط: عدّل هذا النمط ليناسب قناتك
    pattern = r"مسلسل (.+?) - الموسم (\d+) - الحلقة (\d+)"
    match = re.search(pattern, text)
    if match:
        return match.group(1), int(match.group(2)), int(match.group(3))
    return None, None, None

def save_to_db(series_name, season, episode_num, telegram_msg_id):
    """دالة لحفظ الحلقة في قاعدة البيانات."""
    try:
        with engine.connect() as conn:
            # 1. تحقق إذا كان المسلسل موجودًا أو أضفه
            result = conn.execute(
                text("SELECT id FROM series WHERE name = :name"),
                {"name": series_name}
            ).fetchone()
            if not result:
                conn.execute(
                    text("INSERT INTO series (name) VALUES (:name)"),
                    {"name": series_name}
                )
                conn.commit()
                result = conn.execute(
                    text("SELECT id FROM series WHERE name = :name"),
                    {"name": series_name}
                ).fetchone()

            series_id = result[0]
            # 2. أضف الحلقة
            conn.execute(
                text("""
                    INSERT INTO episodes (series_id, season, episode_number, telegram_message_id)
                    VALUES (:sid, :season, :ep, :msg_id)
                    ON CONFLICT DO NOTHING
                """),
                {"sid": series_id, "season": season, "ep": episode_num, "msg_id": telegram_msg_id}
            )
            conn.commit()
            print(f"✅ تمت إضافة: {series_name} S{season}E{episode_num}")
    except Exception as e:
        print(f"❌ خطأ في حفظ البيانات: {e}")

if __name__ == "__main__":
    asyncio.run(main())
