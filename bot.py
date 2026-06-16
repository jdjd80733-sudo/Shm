import os
import re
import json
import subprocess
import threading
import time
import requests
import asyncio
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.constants import ParseMode
from groq import Groq
from googletrans import Translator
from gtts import gTTS
import yt_dlp

# ========== إعدادات (من متغيرات البيئة) ==========
BOT_TOKEN     = os.environ.get("BOT_TOKEN", "ضع_التوكن_هنا")
ADMIN_ID      = int(os.environ.get("ADMIN_ID", "0"))
ADMIN_PASS    = os.environ.get("ADMIN_PASSWORD", "shahem2026")
GROQ_API_KEY  = os.environ.get("GROQ_API_KEY", "ضع_مفتاح_groq_هنا")
SUB_PRICE     = 100
OWNER         = "@S_Hm_8"
YOUR_URL      = os.environ.get("YOUR_URL", "https://your-app.onrender.com")

SHAHEM = "💀 شهم الدكتاتوري | @S_Hm_8"
DB_FILE      = "users.json"
PENDING_FILE = "pending.json"

# ========== اللغات ==========
LANGUAGES = {
    "ar": "🇸🇦 العربية", "en": "🇺🇸 English",  "fr": "🇫🇷 Français",
    "es": "🇪🇸 Español",  "de": "🇩🇪 Deutsch",  "it": "🇮🇹 Italiano",
    "pt": "🇵🇹 Português","ru": "🇷🇺 Русский",  "ja": "🇯🇵 日本語",
    "ko": "🇰🇷 한국어",    "zh": "🇨🇳 中文",     "hi": "🇮🇳 हिन्दी",
    "tr": "🇹🇷 Türkçe",  "nl": "🇳🇱 Nederlands",
}

# ========== سرعات الصوت ==========
SPEEDS = {
    "slow":   ("🐢 بطيء",  True),
    "normal": ("🚶 عادي",  False),
    "fast":   ("⚡ سريع",  False),
}

active_jobs = {}
groq_client = Groq(api_key=GROQ_API_KEY)
translator  = Translator()

# ========== قاعدة البيانات ==========
def load(f):
    try:
        return json.load(open(f, encoding="utf-8")) if os.path.exists(f) else {}
    except:
        return {}

def save(f, d):
    json.dump(d, open(f, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

def is_subscribed(uid):
    u = load(DB_FILE).get(str(uid), {})
    if u.get("admin"): return True, "👑 مدير"
    if u.get("end"):
        try:
            if datetime.fromisoformat(u["end"]) > datetime.now():
                days = (datetime.fromisoformat(u["end"]) - datetime.now()).days
                return True, f"✅ {days} يوم متبقي"
            return False, "❌ الاشتراك منتهي"
        except: pass
    return False, "❌ غير مشترك"

def add_sub(uid, months=1):
    db = load(DB_FILE)
    u  = db.get(str(uid), {})
    now = datetime.now()
    try:
        cur = datetime.fromisoformat(u.get("end",""))
        base = cur if cur > now else now
    except:
        base = now
    u["start"] = now.isoformat()
    u["end"]   = (base + timedelta(days=30*months)).isoformat()
    db[str(uid)] = u
    save(DB_FILE, db)

def make_admin(uid):
    db = load(DB_FILE)
    u  = db.get(str(uid), {})
    u["admin"] = True
    u["end"]   = "2099-12-31"
    db[str(uid)] = u
    save(DB_FILE, db)

# ========== أزرار ==========
def lang_buttons():
    kb, row = [], []
    for c, n in LANGUAGES.items():
        row.append(InlineKeyboardButton(n, callback_data=f"L_{c}"))
        if len(row) == 2: kb.append(row); row = []
    if row: kb.append(row)
    return InlineKeyboardMarkup(kb)

def speed_buttons(lang):
    row = [InlineKeyboardButton(label, callback_data=f"S_{lang}_{k}") for k,(label,_) in SPEEDS.items()]
    return InlineKeyboardMarkup([row, [InlineKeyboardButton("🔙 تغيير اللغة", callback_data="BACK_LANG")]])

# ========== يوتيوب ==========
def is_yt(text):
    if not text: return False
    return bool(re.search(r"(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[\w\-]+", text))

def dl_yt(url, out):
    opts = {
        "format": "bestvideo[height<=720]+bestaudio/best",
        "outtmpl": out, "quiet": True,
        "merge_output_format": "mp4",
        "concurrent_fragments": 4, "retries": 3,
    }
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=True)
        return info.get("title",""), info.get("duration", 0)

def fmt_duration(sec):
    sec = int(sec)
    if sec >= 3600:
        return f"{sec//3600}س {(sec%3600)//60}د"
    return f"{sec//60}د {sec%60}ث"

# ========== تقسيم الصوت لأجزاء 25MB (حد Groq) ==========
def split_audio_for_groq(wav_path, chunk_minutes=10):
    """يقسم الصوت إلى أجزاء 10 دقائق لأن Groq يقبل 25MB كحد أقصى"""
    result = subprocess.run(
        ["ffprobe","-v","error","-show_entries","format=duration",
         "-of","default=noprint_wrappers=1:nokey=1", wav_path],
        capture_output=True, text=True
    )
    try: duration = float(result.stdout.strip())
    except: duration = 0

    chunks, start, i = [], 0, 0
    step = chunk_minutes * 60
    while start < duration:
        chunk = wav_path.replace(".wav", f"_c{i}.mp3")
        subprocess.run([
            "ffmpeg","-i", wav_path,
            "-ss", str(start), "-t", str(step),
            "-ar","16000","-ac","1","-b:a","64k",
            chunk,"-y"
        ], capture_output=True)
        chunks.append(chunk)
        start += step
        i += 1
    return chunks

# ========== Groq Whisper ==========
def transcribe_with_groq(audio_path):
    """إرسال الصوت لـ Groq ويرجع النص — مجاناً وسريع جداً"""
    with open(audio_path, "rb") as f:
        result = groq_client.audio.transcriptions.create(
            file=(os.path.basename(audio_path), f),
            model="whisper-large-v3",   # أقوى موديل مجاني في Groq
            response_format="text",
            language=None,              # يكتشف اللغة تلقائياً
        )
    return result if isinstance(result, str) else result.text

# ========== Keep-Alive ==========
class Ping(BaseHTTPRequestHandler):
    def do_GET(s): s.send_response(200); s.end_headers(); s.wfile.write(b"OK")
    def log_message(s,*a): pass

threading.Thread(target=lambda: HTTPServer(("0.0.0.0",8080),Ping).serve_forever(), daemon=True).start()

def self_ping():
    while True:
        time.sleep(240)
        try: requests.get(YOUR_URL, timeout=10)
        except: pass

threading.Thread(target=self_ping, daemon=True).start()

# ========== أوامر ==========
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ok, st = is_subscribed(update.effective_user.id)
    await update.message.reply_text(
        f"👋 *أهلاً في بوت الدبلجة!*\n\n"
        f"📊 اشتراكك: {st}\n\n"
        f"━━━━━━━━━━━━\n"
        f"🎬 أرسل رابط يوتيوب\n"
        f"🌍 اختر اللغة\n"
        f"⚡ اختر السرعة\n"
        f"✅ استلم الفيديو المدبلج!\n\n"
        f"━━━━━━━━━━━━\n"
        f"💎 /pay — اشتراك ({SUB_PRICE} نجمة)\n"
        f"📊 /status — حالتي\n"
        f"❓ /help — مساعدة\n\n"
        f"_{SHAHEM}_", parse_mode=ParseMode.MARKDOWN
    )

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"❓ *المساعدة*\n\n"
        f"🎙️ *محرك الدبلجة:*\n"
        f"• Groq Whisper Large V3 ☁️\n"
        f"• أقوى موديل متاح مجاناً\n"
        f"• يدعم مقاطع حتى *5 ساعات* ✅\n\n"
        f"🌍 *اللغات:* 14 لغة\n"
        f"⚡ *السرعات:* بطيء / عادي / سريع\n\n"
        f"⚠️ *ملاحظات:*\n"
        f"• مقطع 5 ساعات يحتاج ~30 دقيقة معالجة\n"
        f"• لا ترسل رابطين في نفس الوقت\n\n"
        f"_{SHAHEM}_", parse_mode=ParseMode.MARKDOWN
    )

async def pay(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"💎 *الاشتراك الشهري*\n\n"
        f"💰 {SUB_PRICE} نجمة / شهر\n\n"
        f"⭐ اضغط اسم البوت ← نجوم ← أرسل {SUB_PRICE}\n"
        f"✅ يتفعل تلقائياً\n\n"
        f"📞 {OWNER}\n_{SHAHEM}_", parse_mode=ParseMode.MARKDOWN
    )

async def status_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ok, st = is_subscribed(uid)
    u = load(DB_FILE).get(str(uid), {})
    end = ""
    if u.get("end") and u["end"] != "2099-12-31":
        try: end = f"\n📅 ينتهي: {datetime.fromisoformat(u['end']).strftime('%Y-%m-%d')}"
        except: pass
    await update.message.reply_text(f"📊 *حالة اشتراكك*\n\n{st}{end}\n\n_{SHAHEM}_", parse_mode=ParseMode.MARKDOWN)

async def admin_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.args and ctx.args[0] == ADMIN_PASS:
        make_admin(update.effective_user.id)
        await update.message.reply_text(f"👑 تم التفعيل\n_{SHAHEM}_", parse_mode=ParseMode.MARKDOWN)

async def add_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not load(DB_FILE).get(str(update.effective_user.id),{}).get("admin"): return
    try:
        tid = int(ctx.args[0])
        m   = int(ctx.args[1]) if len(ctx.args)>1 else 1
        add_sub(tid, m)
        await update.message.reply_text(f"✅ {m} شهر لـ {tid}")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

async def stats_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not load(DB_FILE).get(str(update.effective_user.id),{}).get("admin"): return
    db = load(DB_FILE)
    now = datetime.now().isoformat()
    await update.message.reply_text(
        f"📊 *الإحصائيات*\n\n"
        f"👥 الكل: {len(db)}\n"
        f"✅ نشطون: {sum(1 for u in db.values() if u.get('end','') > now)}\n"
        f"👑 مديرون: {sum(1 for u in db.values() if u.get('admin'))}\n\n"
        f"_{SHAHEM}_", parse_mode=ParseMode.MARKDOWN
    )

async def broadcast_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not load(DB_FILE).get(str(update.effective_user.id),{}).get("admin"): return
    if not ctx.args: return
    msg = " ".join(ctx.args)
    sent = failed = 0
    for uid in load(DB_FILE):
        try:
            await ctx.bot.send_message(int(uid), f"📢 *إشعار*\n\n{msg}\n\n_{SHAHEM}_", parse_mode=ParseMode.MARKDOWN)
            sent += 1
        except: failed += 1
    await update.message.reply_text(f"✅ {sent} | ❌ {failed}")

async def handle_stars(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tx = update.message.successful_payment
    if not tx: return
    uid    = update.effective_user.id
    amount = tx.total_amount
    months = max(1, amount // SUB_PRICE)
    add_sub(uid, months)
    name = update.effective_user.first_name or ""
    user = update.effective_user.username or "بدون"
    await update.message.reply_text(
        f"🎉 *شكراً!*\n💎 {amount}⭐ = {months} شهر\n✅ تم التفعيل\n_{SHAHEM}_",
        parse_mode=ParseMode.MARKDOWN
    )
    await ctx.bot.send_message(
        ADMIN_ID,
        f"💰 اشتراك جديد!\n👤 {name}\n@{user}\n🆔 `{uid}`\n💎 {amount}⭐ — {months} شهر",
        parse_mode=ParseMode.MARKDOWN
    )

# ========== معالجة الفيديو ==========
async def proc(update, msg, vid, lang, speed_key, title, ctx):
    uid = update.callback_query.from_user.id
    chat_id = update.callback_query.message.chat_id
    speed_label, slow = SPEEDS.get(speed_key, SPEEDS["normal"])

    prefix = f"job_{uid}_{int(time.time())}"
    aw  = f"{prefix}.wav"
    oa  = f"{prefix}_tts.mp3"
    ov  = f"{prefix}_out.mp4"
    chunks = []

    try:
        # 1. استخراج الصوت
        await msg.edit_text("🎵 استخراج الصوت...")
        subprocess.run([
            "ffmpeg","-i",vid,
            "-vn","-acodec","pcm_s16le",
            "-ar","16000","-ac","1",
            aw,"-y"
        ], check=True, capture_output=True)

        # 2. تقسيم وإرسال لـ Groq
        await msg.edit_text("☁️ إرسال الصوت لـ Groq Whisper...")
        chunks = split_audio_for_groq(aw, chunk_minutes=10)
        all_text = []
        for i, chunk in enumerate(chunks):
            await msg.edit_text(f"📝 تحويل الصوت لنص... ({i+1}/{len(chunks)})")
            text_part = transcribe_with_groq(chunk)
            all_text.append(text_part)
            await asyncio.sleep(1)  # تجنب rate limit
        txt = " ".join(all_text).strip()

        if not txt:
            await msg.edit_text("❌ لم يُكتشف كلام في المقطع"); return

        # 3. الترجمة
        await msg.edit_text("🌍 جاري الترجمة...")
        try:
            src = translator.detect(txt[:500]).lang
            if src != lang:
                words = txt.split()
                parts = []
                for i in range(0, len(words), 400):
                    batch = " ".join(words[i:i+400])
                    parts.append(translator.translate(batch, src=src, dest=lang).text)
                    await asyncio.sleep(0.5)
                res = " ".join(parts)
            else:
                res = txt
        except Exception as e:
            await msg.edit_text(f"❌ خطأ في الترجمة: {e}"); return

        # 4. تحويل النص لصوت
        await msg.edit_text("🔊 توليد الصوت...")
        gTTS(text=res, lang=lang, slow=slow).save(oa)

        # تعديل السرعة إذا كانت سريعة
        if speed_key == "fast":
            fast = f"{prefix}_fast.mp3"
            subprocess.run([
                "ffmpeg","-i",oa,"-filter:a","atempo=1.3",fast,"-y"
            ], capture_output=True)
            oa = fast

        # 5. دمج
        await msg.edit_text("🎬 دمج الصوت مع الفيديو...")
        subprocess.run([
            "ffmpeg","-i",vid,"-i",oa,
            "-map","0:v:0","-map","1:a:0",
            "-c:v","copy","-c:a","aac","-b:a","192k",
            "-shortest",ov,"-y"
        ], check=True, capture_output=True)

        sz = os.path.getsize(ov)/1024/1024
        await msg.edit_text(f"📤 إرسال ({sz:.1f}MB)...")

        cap = (f"🎬 *{title[:50]}*\n" if title else "") + \
              f"🌍 {LANGUAGES.get(lang,lang)}\n" \
              f"🎙️ {speed_label}\n_{SHAHEM}_"

        with open(ov,"rb") as f:
            if sz <= 50:
                await ctx.bot.send_video(chat_id=chat_id, video=f, caption=cap,
                    parse_mode=ParseMode.MARKDOWN, supports_streaming=True)
            else:
                await ctx.bot.send_document(chat_id=chat_id, document=f, caption=cap,
                    parse_mode=ParseMode.MARKDOWN)
        await msg.delete()

    except Exception as e:
        await msg.edit_text(f"❌ خطأ: {str(e)[:300]}")
    finally:
        for f in [aw, oa, ov, vid] + chunks:
            try:
                if f and os.path.exists(f): os.remove(f)
            except: pass
        active_jobs.pop(str(uid), None)

# ========== معالجات ==========
async def msg_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    txt = update.message.text or ""
    if not is_yt(txt):
        await update.message.reply_text(f"📎 أرسل رابط يوتيوب\n_{SHAHEM}_", parse_mode=ParseMode.MARKDOWN)
        return
    ok, st = is_subscribed(uid)
    if not ok:
        await update.message.reply_text(f"❌ {st}\n💎 /pay\n_{SHAHEM}_", parse_mode=ParseMode.MARKDOWN); return
    if str(uid) in active_jobs:
        await update.message.reply_text("⏳ لديك مقطع يُعالج الآن، انتظر حتى ينتهي"); return
    p = load(PENDING_FILE)
    p[str(uid)] = {"url": txt}
    save(PENDING_FILE, p)
    await update.message.reply_text(
        f"🌍 *اختر لغة الدبلجة:*\n_{SHAHEM}_",
        parse_mode=ParseMode.MARKDOWN, reply_markup=lang_buttons()
    )

async def btn_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid, d = q.from_user.id, q.data

    if d == "BACK_LANG":
        await q.edit_message_text(f"🌍 *اختر لغة الدبلجة:*\n_{SHAHEM}_",
            parse_mode=ParseMode.MARKDOWN, reply_markup=lang_buttons()); return

    if d.startswith("L_"):
        lang = d[2:]
        p = load(PENDING_FILE)
        if str(uid) not in p:
            await q.edit_message_text(f"❌ انتهت الجلسة، أرسل الرابط من جديد"); return
        p[str(uid)]["lang"] = lang
        save(PENDING_FILE, p)
        await q.edit_message_text(
            f"✅ {LANGUAGES.get(lang,lang)}\n\n🎙️ *اختر سرعة الصوت:*\n_{SHAHEM}_",
            parse_mode=ParseMode.MARKDOWN, reply_markup=speed_buttons(lang)); return

    if d.startswith("S_"):
        _, lang, speed_key = d.split("_")
        p = load(PENDING_FILE)
        url = p.get(str(uid),{}).get("url","")
        if not url:
            await q.edit_message_text(f"❌ انتهت الجلسة، أرسل الرابط من جديد"); return
        if str(uid) in active_jobs:
            await q.edit_message_text("⏳ لديك مقطع يُعالج الآن"); return
        active_jobs[str(uid)] = True
        mg = await q.edit_message_text("⬇️ جاري تحميل الفيديو...")
        vf = f"yt_{uid}_{int(time.time())}.mp4"
        try:
            title, dur = dl_yt(url, vf)
            await mg.edit_text(f"✅ {title[:40]}\n⏱️ {fmt_duration(dur)}\n\n⚙️ جاري المعالجة...")
            await proc(update, mg, vf, lang, speed_key, title, ctx)
        except Exception as e:
            await mg.edit_text(f"❌ فشل التحميل: {str(e)[:200]}")
            active_jobs.pop(str(uid), None)
        finally:
            p.pop(str(uid), None)
            save(PENDING_FILE, p)

# ========== تشغيل ==========
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("help",      help_cmd))
    app.add_handler(CommandHandler("pay",       pay))
    app.add_handler(CommandHandler("status",    status_cmd))
    app.add_handler(CommandHandler("admin",     admin_cmd))
    app.add_handler(CommandHandler("add",       add_cmd))
    app.add_handler(CommandHandler("stats",     stats_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, handle_stars))
    app.add_handler(CallbackQueryHandler(btn_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg_handler))
    print(f"🤖 البوت يعمل | {SHAHEM}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()