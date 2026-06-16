import os
import re
import subprocess
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from faster_whisper import WhisperModel
from googletrans import Translator
from gtts import gTTS
import yt_dlp

BOT_TOKEN = "8874256208:AAHvYFBZLK5cGGOa8NUVfKwrnMO4nqVh8y8"

print("⏳ جاري تحميل موديل Whisper...")
model = WhisperModel("base", device="cpu", compute_type="int8")
translator = Translator()
print("✅ الموديل جاهز!")


def is_youtube_url(text):
    pattern = r"(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[\w\-]+"
    return re.search(pattern, text)


def download_youtube(url, output_path):
    ydl_opts = {
        "format": "mp4/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",
        "outtmpl": output_path,
        "max_filesize": 50 * 1024 * 1024,
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return info.get("title", "فيديو يوتيوب")


async def process_video(update, msg, input_video, video_title=""):
    input_audio = input_video.replace(".mp4", "_audio.wav")
    output_audio = input_video.replace(".mp4", "_dubbed.mp3")
    output_video = input_video.replace(".mp4", "_output.mp4")

    try:
        await msg.edit_text("✅ تم تحميل الفيديو\n⏳ جاري استخراج الصوت...")

        subprocess.run([
            "ffmpeg", "-i", input_video,
            "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1",
            input_audio, "-y"
        ], check=True, capture_output=True)

        await msg.edit_text("✅ تم استخراج الصوت\n⏳ جاري تحويل الكلام لنص...")

        segments, _ = model.transcribe(input_audio, language="en")
        english_text = " ".join([seg.text for seg in segments])

        if not english_text.strip():
            await msg.edit_text("❌ لم يتم العثور على كلام إنجليزي في الفيديو!")
            return

        await msg.edit_text("✅ تم تحويل الكلام لنص\n⏳ جاري الترجمة للعربي...")

        translated = translator.translate(english_text, src="en", dest="ar")
        arabic_text = translated.text

        await msg.edit_text("✅ تمت الترجمة\n⏳ جاري تحويل النص لصوت عربي...")

        tts = gTTS(text=arabic_text, lang="ar", slow=False)
        tts.save(output_audio)

        await msg.edit_text("✅ تم إنشاء الصوت العربي\n🎬 جاري دمج الصوت مع الفيديو...")

        subprocess.run([
            "ffmpeg", "-i", input_video,
            "-i", output_audio,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-shortest",
            output_video, "-y"
        ], check=True, capture_output=True)

        await msg.edit_text("✅ تم! جاري إرسال الفيديو المدبلج...")

        with open(output_video, "rb") as f:
            caption = f"🎬 *{video_title}*\n\n" if video_title else ""
            caption += f"📝 *النص الإنجليزي:*\n{english_text[:300]}\n\n"
            caption += f"🇸🇦 *الترجمة العربية:*\n{arabic_text[:300]}"
            await update.message.reply_video(
                video=f,
                caption=caption,
                parse_mode="Markdown"
            )

        await msg.delete()

    finally:
        for f in [input_audio, output_audio, output_video]:
            if os.path.exists(f):
                os.remove(f)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if is_youtube_url(text):
        msg = await update.message.reply_text("🔗 تم استلام رابط يوتيوب\n⏳ جاري تحميل الفيديو...")
        input_video = f"yt_{update.message.message_id}.mp4"
        try:
            title = download_youtube(text, input_video)
            await process_video(update, msg, input_video, title)
        except Exception as e:
            await msg.edit_text(f"❌ حدث خطأ: {str(e)}")
        finally:
            if os.path.exists(input_video):
                os.remove(input_video)
    else:
        await update.message.reply_text(
            "📎 أرسل رابط يوتيوب أو فيديو مباشرة!\n"
            "مثال: https://youtube.com/watch?v=..."
        )


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("📥 جاري تحميل الفيديو...")
    input_video = f"vid_{update.message.message_id}.mp4"
    try:
        video = update.message.video or update.message.document
        file = await context.bot.get_file(video.file_id)
        await file.download_to_drive(input_video)
        await process_video(update, msg, input_video)
    except Exception as e:
        await msg.edit_text(f"❌ حدث خطأ: {str(e)}")
    finally:
        if os.path.exists(input_video):
            os.remove(input_video)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 أهلاً! أنا بوت شهم للدبلجة العربية 🎬\n\n"
        "يمكنني دبلجة الفيديوهات من الإنجليزي للعربي!\n\n"
        "📌 *طريقة الاستخدام:*\n"
        "• أرسل رابط يوتيوب مباشرة\n"
        "• أو أرسل فيديو مرفق\n\n"
        "⚠️ *ملاحظات:*\n"
        "• الفيديو يجب أن يكون بالإنجليزي\n"
        "• الحد الأقصى للفيديو المرفق 20MB\n"
        "• روابط يوتيوب تعمل بدون حد",
        parse_mode="Markdown"
    )


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.Regex(r"^/start"), start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))
    print("🤖 البوت شغال!")
    app.run_polling()


if __name__ == "__main__":
    main()