import socket
import asyncio
import aiohttp
import logging
import os
import uuid
import requests
import yt_dlp
import telebot
from telebot import types
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB

# ═══════════════════════════════════════════════════════
#  ⚙️  CONFIG  —  EDIT THIS SECTION ONLY
# ═══════════════════════════════════════════════════════
#
#  1. BOT_TOKEN         → Get from @BotFather on Telegram
#                         Type /newbot, follow steps, copy token
#
#  2. CHANNEL_USERNAME  → Your Telegram channel username
#                         Must start with @
#                         Bot MUST be admin in this channel
#
#  3. CHANNEL_LINK      → Full t.me link to your channel
#
# ═══════════════════════════════════════════════════════

BOT_TOKEN = "PASTE_YOUR_BOT_TOKEN_HERE"
CHANNEL_USERNAME = "@your_channel_username"
CHANNEL_LINK = "https://t.me/your_channel_username"

# ═══════════════════════════════════════════════════════

log = logging.getLogger("luxes")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

# ═══════════════════════════════════════════
# DoH BYPASS (Nepal ISP Telegram block)
# ═══════════════════════════════════════════
DOH_PROVIDERS = [
    "https://dns.google/resolve",
    "https://dns.quad9.net:5053/dns-query",
    "https://dns.adguard-dns.com/dns-query",
    "https://cloudflare-dns.com/dns-query",
    "https://1.1.1.1/dns-query",
]

_dns_cache = {}

async def cf_resolve(hostname):
    if hostname in _dns_cache:
        return _dns_cache[hostname]
    for provider in DOH_PROVIDERS:
        try:
            url = f"{provider}?name={hostname}&type=A"
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    url,
                    headers={"accept": "application/dns-json"},
                    timeout=aiohttp.ClientTimeout(total=6),
                ) as r:
                    if r.status != 200:
                        continue
                    data = await r.json()
            for a in (data.get("Answer") or []):
                if a.get("type") == 1 and a.get("data"):
                    ip = str(a["data"]).strip()
                    if ":" in ip:
                        continue
                    log.info(f"✅ DoH {hostname} → {ip}")
                    _dns_cache[hostname] = ip
                    return ip
        except Exception as e:
            log.warning(f"DoH {provider.split('/')[2]} {hostname}: {type(e).__name__}")
            continue
    log.warning(f"❌ All DoH failed for {hostname}")
    return None


_orig_getaddrinfo = socket.getaddrinfo

def _patched_getaddrinfo(host, port, *args, **kwargs):
    if host in ("api.telegram.org", "api.telegram.com"):
        try:
            loop = asyncio.new_event_loop()
            ip = loop.run_until_complete(cf_resolve(host))
            loop.close()
            if ip:
                return _orig_getaddrinfo(ip, port, *args, **kwargs)
        except Exception as e:
            log.warning(f"patch failed: {e}")
    return _orig_getaddrinfo(host, port, *args, **kwargs)

socket.getaddrinfo = _patched_getaddrinfo
# ═══════════════════════════════════════════

bot = telebot.TeleBot(BOT_TOKEN)

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

pending = {}
verified = set()


# ═══════════════════════════════════════════
# METADATA TAGGER
# ═══════════════════════════════════════════
def add_metadata(mp3_path, title, artist, album, thumb_url):
    try:
        audio = MP3(mp3_path, ID3=ID3)
        try:
            audio.add_tags()
        except Exception:
            pass

        audio.tags.add(TIT2(encoding=3, text=title))
        audio.tags.add(TPE1(encoding=3, text=artist))
        audio.tags.add(TALB(encoding=3, text=album))

        if thumb_url:
            try:
                r = requests.get(thumb_url, timeout=10)
                if r.status_code == 200:
                    audio.tags.delall("APIC")
                    audio.tags.add(APIC(
                        encoding=3,
                        mime="image/jpeg",
                        type=3,
                        desc="Cover",
                        data=r.content,
                    ))
            except Exception as e:
                log.warning(f"Thumb embed failed: {e}")

        audio.save(v2_version=3)
        log.info(f"✅ Metadata → {title} · {artist}")
        return True
    except Exception as e:
        log.warning(f"❌ Metadata failed: {e}")
        return False


# ═══════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════
def is_joined(user_id):
    try:
        m = bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return m.status in ("member", "administrator", "creator")
    except Exception:
        return False


def send_join_prompt(chat_id):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK),
        types.InlineKeyboardButton("✅ I've Joined", callback_data="check_join"),
    )
    bot.send_message(
        chat_id,
        "╔══════════════════════╗\n"
        "   ❤️ *JOIN CHANNEL* ❤️\n"
        "╚══════════════════════╝\n\n"
        "⚠️ *Join our channel first* to use this bot.\n\n"
        "👇 Tap *Join*, then tap *I've Joined*",
        parse_mode="Markdown",
        reply_markup=kb,
    )


def send_welcome(chat_id, first_name="bro"):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK),
        types.InlineKeyboardButton("❓ How to Use", callback_data="howto"),
        types.InlineKeyboardButton("ℹ️ About", callback_data="about"),
    )
    bot.send_message(
        chat_id,
        f"╔══════════════════════╗\n"
        f"   🎵 *LUXES BOT* 🎵\n"
        f"╚══════════════════════╝\n\n"
        f"Hey *{first_name}* 👋\n\n"
        f"✅ Free MP3 Download Bot\n"
        f"✅ No Premium • No Credits\n"
        f"✅ Free For Everyone\n\n"
        f"📌 Just send me a YouTube link!",
        parse_mode="Markdown",
        reply_markup=kb,
    )


# ═══════════════════════════════════════════
# SET MENU COMMANDS
# ═══════════════════════════════════════════
def setup_bot_menu():
    commands = [
        types.BotCommand("start", "🚀 Start the bot"),
        types.BotCommand("help", "❓ How to use"),
        types.BotCommand("about", "ℹ️ About LUXES BOT"),
        types.BotCommand("channel", "📢 Join our channel"),
    ]
    bot.set_my_commands(commands)
    log.info("✅ Bot menu commands set")


# ═══════════════════════════════════════════
# COMMAND HANDLERS
# ═══════════════════════════════════════════
@bot.message_handler(commands=["start"])
def cmd_start(message):
    uid = message.from_user.id
    chat_id = message.chat.id
    if uid in verified or is_joined(uid):
        verified.add(uid)
        send_welcome(chat_id, message.from_user.first_name or "bro")
    else:
        send_join_prompt(chat_id)


@bot.message_handler(commands=["help"])
def cmd_help(message):
    bot.send_message(
        message.chat.id,
        "📖 *How to Use*\n\n"
        "1️⃣ Send a YouTube link\n"
        "2️⃣ Pick quality (128 / 192 / 320 kbps)\n"
        "3️⃣ Wait for the MP3\n"
        "4️⃣ Done! 🎵",
        parse_mode="Markdown",
    )


@bot.message_handler(commands=["about"])
def cmd_about(message):
    bot.send_message(
        message.chat.id,
        "ℹ️ *LUXES BOT*\n\n"
        "🎵 Free YouTube → MP3 converter\n"
        "✅ No premium\n"
        "✅ No credits\n"
        "✅ 100% free forever\n\n"
        "❤️ Powered by LUXES UNITED",
        parse_mode="Markdown",
    )


@bot.message_handler(commands=["channel"])
def cmd_channel(message):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK))
    bot.send_message(
        message.chat.id,
        "📢 *Join our channel* for free bots and codes!",
        parse_mode="Markdown",
        reply_markup=kb,
    )


# ═══════════════════════════════════════════
# CALLBACKS
# ═══════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data == "check_join")
def cb_check_join(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    if is_joined(uid):
        verified.add(uid)
        bot.answer_callback_query(call.id, "✅ Verified!")
        try:
            bot.delete_message(chat_id, call.message.message_id)
        except Exception:
            pass
        send_welcome(chat_id, call.from_user.first_name or "bro")
    else:
        bot.answer_callback_query(call.id, "❌ Join first!", show_alert=True)


@bot.callback_query_handler(func=lambda c: c.data == "howto")
def cb_howto(call):
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        "📖 *How to Use*\n\n"
        "1️⃣ Send a YouTube link\n"
        "2️⃣ Pick quality\n"
        "3️⃣ Wait for MP3 🎵",
        parse_mode="Markdown",
    )


@bot.callback_query_handler(func=lambda c: c.data == "about")
def cb_about(call):
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        "ℹ️ *LUXES BOT*\n\n"
        "Free MP3 downloader.\n"
        "No premium • No credits.\n\n"
        "❤️ LUXES UNITED",
        parse_mode="Markdown",
    )


# ═══════════════════════════════════════════
# YOUTUBE LINK HANDLER
# ═══════════════════════════════════════════
@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_link(message):
    uid = message.from_user.id
    chat_id = message.chat.id

    if uid not in verified:
        if not is_joined(uid):
            send_join_prompt(chat_id)
            return
        verified.add(uid)

    url = message.text.strip()
    if "youtube.com" not in url and "youtu.be" not in url:
        bot.reply_to(message, "❌ Send a valid YouTube link")
        return

    msg = bot.reply_to(message, "🔍 Fetching info... ⏳")
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)

        title = info.get("title", "Unknown")
        artist = info.get("uploader") or info.get("channel") or "Unknown Artist"
        thumb = info.get("thumbnail")
        duration = info.get("duration", 0) or 0
        mins, secs = divmod(duration, 60)

        pending[chat_id] = {"url": url, "title": title, "artist": artist, "thumb": thumb}

        kb = types.InlineKeyboardMarkup(row_width=3)
        kb.add(
            types.InlineKeyboardButton("128 kbps", callback_data="q_128"),
            types.InlineKeyboardButton("192 kbps", callback_data="q_192"),
            types.InlineKeyboardButton("320 kbps", callback_data="q_320"),
        )
        caption = f"🎵 *{title}*\n👤 {artist}\n⏱ {mins}:{secs:02d}\n\n🎚 *Pick quality:*"

        if thumb:
            bot.send_photo(chat_id, thumb, caption=caption, parse_mode="Markdown", reply_markup=kb)
        else:
            bot.send_message(chat_id, caption, parse_mode="Markdown", reply_markup=kb)

        bot.delete_message(chat_id, msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)[:200]}", chat_id, msg.message_id)


# ═══════════════════════════════════════════
# QUALITY SELECTED
# ═══════════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data.startswith("q_"))
def cb_quality(call):
    chat_id = call.message.chat.id
    uid = call.from_user.id
    quality = call.data.split("_")[1]

    if uid not in verified and not is_joined(uid):
        bot.answer_callback_query(call.id, "❌ Join channel first!", show_alert=True)
        send_join_prompt(chat_id)
        return

    if chat_id not in pending:
        bot.answer_callback_query(call.id, "Send the link again")
        return

    data = pending[chat_id]
    url = data["url"]
    title = data["title"]
    artist = data["artist"]
    thumb = data["thumb"]

    bot.answer_callback_query(call.id, f"⚙️ {quality}kbps...")
    status = bot.send_message(
        chat_id,
        f"⬇️ Downloading + converting to *{quality}kbps*... ⏳",
        parse_mode="Markdown",
    )

    file_id = str(uuid.uuid4())[:8]
    out_path = os.path.join(DOWNLOAD_DIR, file_id)

    try:
        ydl_opts = {
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": quality,
            }],
            "outtmpl": out_path,
            "quiet": True,
            "no_warnings": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        mp3_file = out_path + ".mp3"
        if not os.path.exists(mp3_file):
            raise Exception("MP3 not created")

        add_metadata(mp3_file, title, artist, "LUXES UNITED", thumb)

        size_mb = os.path.getsize(mp3_file) / (1024 * 1024)
        if size_mb > 50:
            bot.edit_message_text(
                f"❌ Too big ({size_mb:.1f}MB). Telegram limit is 50MB.",
                chat_id, status.message_id,
            )
            os.remove(mp3_file)
            return

        with open(mp3_file, "rb") as f:
            bot.send_audio(
                chat_id,
                f,
                title=title,
                performer=artist,
                caption=f"✅ *{quality} kbps* · {size_mb:.1f} MB\n\n❤️ From LUXES UNITED",
                parse_mode="Markdown",
            )

        bot.delete_message(chat_id, status.message_id)
        os.remove(mp3_file)
        pending.pop(chat_id, None)

    except Exception as e:
        bot.edit_message_text(
            f"❌ Failed: {str(e)[:200]}",
            chat_id, status.message_id,
        )
        fpath = out_path + ".mp3"
        if os.path.exists(fpath):
            os.remove(fpath)


# ═══════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════
if __name__ == "__main__":
    setup_bot_menu()
    print("🎵 LUXES BOT is running...")
    bot.infinity_polling()