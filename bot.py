#!/usr/bin/env python3
"""
Cuhi Bot — Media Downloader
"""

import asyncio, json, os
from datetime import datetime
from pathlib import Path
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    InputMediaPhoto, InputMediaVideo,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler,
)

# ── CONFIG ────────────────────────────────────────────────────────────────────
TOKEN        = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
ADMIN_IDS    = {7232714487}
DATA_ROOT    = Path("./data")
COOKIES_ROOT = Path("./cookies")

PLATFORMS = {
    "instagram": ("instagram_profiles.txt", "instagram.com_cookies.txt", 5),
    "tiktok":    ("tiktok_profiles.txt",    "tiktok.com_cookies.txt",    3),
    "facebook":  ("facebook_profiles.txt",  "facebook.com_cookies.txt",  5),
    "x":         ("x_profiles.txt",         "x.com_cookies.txt",         5),
}
PLATFORM_URLS = {
    "instagram": "https://www.instagram.com/",
    "tiktok":    "https://www.tiktok.com/@",
    "facebook":  "https://www.facebook.com/",
    "x":         "https://x.com/",
}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXT = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v"}

STOP_EVENTS: dict[int, asyncio.Event] = {}

# States
(ST_MAIN, ST_ADD_PLAT, ST_ADD_URL, ST_REM_PLAT, ST_REM_URL,
 ST_MEDIA_CHOICE, ST_SET_CHANNEL, ST_STORY_PLAT, ST_HIGHLIGHT_PLAT,
 ST_STORY_URL, ST_HIGHLIGHT_URL) = range(11)

# ── PER-USER PATHS ────────────────────────────────────────────────────────────
def user_dir(uid):
    p = DATA_ROOT / str(uid); p.mkdir(parents=True, exist_ok=True); return p
def cookie_dir(uid):
    p = COOKIES_ROOT / str(uid); p.mkdir(parents=True, exist_ok=True); return p
def profile_file(uid, platform):
    return user_dir(uid) / PLATFORMS[platform][0]
def history_file(uid):
    return user_dir(uid) / "history.json"
def settings_file(uid):
    return user_dir(uid) / "settings.json"

# ── DATA HELPERS ──────────────────────────────────────────────────────────────
def load_profiles(uid, p):
    f = profile_file(uid, p)
    return [l.strip() for l in f.read_text().splitlines() if l.strip()] if f.exists() else []

def save_profiles(uid, p, urls):
    profile_file(uid, p).write_text("\n".join(urls) + "\n")

def load_history(uid):
    f = history_file(uid)
    return json.loads(f.read_text()) if f.exists() else []

def save_history(uid, entry):
    h = load_history(uid); h.insert(0, entry)
    history_file(uid).write_text(json.dumps(h[:50], indent=2))

def load_settings(uid):
    f = settings_file(uid)
    return json.loads(f.read_text()) if f.exists() else {}

def save_settings(uid, data):
    settings_file(uid).write_text(json.dumps(data, indent=2))

def get_channel(uid):
    return load_settings(uid).get("channel")

def set_channel(uid, channel):
    s = load_settings(uid)
    if channel: s["channel"] = channel
    else: s.pop("channel", None)
    save_settings(uid, s)

def source_count(uid):
    return str(sum(len(load_profiles(uid, p)) for p in PLATFORMS))

def cookie_status(uid):
    ok = [p for p in PLATFORMS if (cookie_dir(uid) / PLATFORMS[p][1]).exists()]
    return ", ".join(ok) if ok else "none"

def total_sent(uid):
    return sum(e.get("sent", 0) for e in load_history(uid))

# ── MENU TEXT ─────────────────────────────────────────────────────────────────
def menu_text(uid, username, name):
    ch      = get_channel(uid)
    ch_line = f"\n📡 Output: *{ch}*" if ch else ""
    return (
        f"@{username}, {name}\n"
        f"🪪 ID: `{uid}`\n"
        f"🆓 Free account\n"
        f"✅ Downloaded Media: *{total_sent(uid)}*\n\n"
        "📩 *Cuhi Bot* — One of the best forwarders from RSS and social networks "
        "(TikTok, Instagram, YouTube, Twitter, Reddit, Facebook, Telegram, VK) to Telegram.\n\n"
        "*Features:*\n"
        "🔀 Private or channel/group modes\n"
        "🔖 Photos, videos and files delivery\n"
        "🚀 Direct Telegram connection\n"
        "🤩 Custom Emojis\n"
        "⚡️ Fast refresh rate\n"
        "✂️ Filters, replacements, message templates, text splitting, etc..\n"
        "🎙 Live streams and premieres processing for videos\n"
        "🕵️\u200d♂️ Moderation and butler modes\n"
        "♻️ Similarity filter\n"
        "🗂 Temporal channel for filtered messages\n"
        "©️ Images watermarks\n"
        "🆘 Technical support\n"
        "👥 Referral program\n\n"
        "*How to Use:*\n"
        "— Add a data source (RSS, Instagram, TikTok, etc.)\n"
        "— Configure message template and filters\n"
        "— Bot will forward & download new posts automatically!\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"  🗂 Sources : *{source_count(uid)}*\n"
        f"  🍪 Cookies : *{cookie_status(uid)}*"
        f"{ch_line}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "👨\u200d💻 Developer: @copyrightpost"
    )

# ── KEYBOARDS ─────────────────────────────────────────────────────────────────
def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add source",       callback_data="m_add"),
         InlineKeyboardButton("🗑️ Remove source",   callback_data="m_remove")],
        [InlineKeyboardButton("📋 My sources",       callback_data="m_list"),
         InlineKeyboardButton("🚀 Run download",     callback_data="m_run")],
        [InlineKeyboardButton("📖 Stories",          callback_data="m_stories"),
         InlineKeyboardButton("🌟 Highlights",       callback_data="m_highlights")],
        [InlineKeyboardButton("⏹️ Stop download",    callback_data="m_stop"),
         InlineKeyboardButton("📜 History",          callback_data="m_history")],
        [InlineKeyboardButton("🍪 Set cookies",      callback_data="m_cookies"),
         InlineKeyboardButton("📊 Status",           callback_data="m_status")],
        [InlineKeyboardButton("📡 Set channel",      callback_data="m_channel")],
    ])

def platform_kb(prefix):
    rows = [[InlineKeyboardButton(p.capitalize(), callback_data=f"{prefix}_{p}")]
            for p in PLATFORMS]
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="m_back")])
    return InlineKeyboardMarkup(rows)

def media_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📷 Photos only",       callback_data="dl_1")],
        [InlineKeyboardButton("🎬 Videos only",       callback_data="dl_2")],
        [InlineKeyboardButton("📦 Both (separately)", callback_data="dl_3")],
        [InlineKeyboardButton("📁 Files (as docs)",   callback_data="dl_4")],
        [InlineKeyboardButton("🔙 Back",              callback_data="m_back")],
    ])

def back_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="m_back")]])

# ── SHOW MENU ─────────────────────────────────────────────────────────────────
async def show_menu(msg, uid, username, name, edit=False):
    text = menu_text(uid, username, name)
    try:
        if edit:
            await msg.edit_text(text, reply_markup=main_menu_kb(), parse_mode="Markdown")
        else:
            await msg.reply_text(text, reply_markup=main_menu_kb(), parse_mode="Markdown")
    except Exception:
        # Fallback plain markdown if MarkdownV2 fails
        plain = (
            f"@{username} — {name}\n"
            f"ID: `{uid}` | Free account\n"
            f"✅ Downloaded Media: *{total_sent(uid)}*\n\n"
            "📩 *Cuhi Bot* — Media Downloader\n\n"
            f"🗂 Sources: *{source_count(uid)}*\n"
            f"🍪 Cookies: *{cookie_status(uid)}*"
        )
        if edit:
            await msg.edit_text(plain, reply_markup=main_menu_kb(), parse_mode="Markdown")
        else:
            await msg.reply_text(plain, reply_markup=main_menu_kb(), parse_mode="Markdown")

# ── /start ────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid  = user.id
    user_dir(uid)
    ctx.user_data["username"] = user.username or "user"
    ctx.user_data["name"]     = user.full_name
    await show_menu(update.effective_message, uid,
                    ctx.user_data["username"], ctx.user_data["name"])
    return ST_MAIN

# ── CALLBACK ROUTER ───────────────────────────────────────────────────────────
async def cb_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q     = update.callback_query
    data  = q.data
    user  = q.from_user
    uid   = user.id
    uname = ctx.user_data.get("username", user.username or "user")
    name  = ctx.user_data.get("name", user.full_name)
    await q.answer()

    # ── BACK ──
    if data == "m_back":
        await show_menu(q.message, uid, uname, name, edit=True)
        return ST_MAIN

    # ── ADD SOURCE ──
    if data == "m_add":
        await q.message.edit_text(
            "➕ *Add source*\nChoose platform:",
            reply_markup=platform_kb("add"), parse_mode="Markdown")
        return ST_ADD_PLAT

    # ── REMOVE SOURCE ──
    if data == "m_remove":
        await q.message.edit_text(
            "🗑️ *Remove source*\nChoose platform:",
            reply_markup=platform_kb("rem"), parse_mode="Markdown")
        return ST_REM_PLAT

    # ── LIST SOURCES ──
    if data == "m_list":
        lines = []
        for p in PLATFORMS:
            urls = load_profiles(uid, p)
            if urls:
                lines.append(f"*{p.upper()}*")
                lines += [f"  • `{u}`" for u in urls]
        text = "\n".join(lines) if lines else "No sources added yet."
        await q.message.edit_text(text, reply_markup=back_kb(), parse_mode="Markdown")
        return ST_MAIN

    # ── RUN DOWNLOAD ──
    if data == "m_run":
        if not any(load_profiles(uid, p) for p in PLATFORMS):
            await q.message.edit_text(
                "⚠️ No sources yet. Add one first.", reply_markup=back_kb())
            return ST_MAIN
        await q.message.edit_text(
            "🚀 *What to download?*\n\n"
            "📷 *Photos only* — sends as images\n"
            "🎬 *Videos only* — sends as videos\n"
            "📦 *Both* — photos first then videos\n"
            "📁 *Files* — sends everything as documents",
            reply_markup=media_kb(), parse_mode="Markdown")
        return ST_MEDIA_CHOICE

    # ── STORIES ──
    if data == "m_stories":
        await q.message.edit_text(
            "📖 *Download Stories*\nChoose platform:",
            reply_markup=platform_kb("story"), parse_mode="Markdown")
        return ST_STORY_PLAT

    # ── HIGHLIGHTS ──
    if data == "m_highlights":
        await q.message.edit_text(
            "🌟 *Download Highlights*\nChoose platform:",
            reply_markup=platform_kb("highlight"), parse_mode="Markdown")
        return ST_HIGHLIGHT_PLAT

    # ── STOP ──
    if data == "m_stop":
        ev = STOP_EVENTS.get(uid)
        if ev and not ev.is_set():
            ev.set()
            await q.message.edit_text(
                "⏹️ *Stop signal sent.*\nFinishing current album then stopping.",
                reply_markup=back_kb(), parse_mode="Markdown")
        else:
            await q.message.edit_text("Nothing is running.", reply_markup=back_kb())
        return ST_MAIN

    # ── HISTORY ──
    if data == "m_history":
        h = load_history(uid)
        if not h:
            text = "No history yet."
        else:
            rows = [f"• {e['date']}  {e['platform']} `{e['user']}`  {e['sent']} files"
                    for e in h[:10]]
            text = "*📜 Last 10 downloads:*\n" + "\n".join(rows)
        await q.message.edit_text(text, reply_markup=back_kb(), parse_mode="Markdown")
        return ST_MAIN

    # ── COOKIES ──
    if data == "m_cookies":
        text = (
            "🍪 *Set cookies*\n\n"
            "Send the file named exactly:\n"
            "`instagram.com_cookies.txt`\n"
            "`tiktok.com_cookies.txt`\n"
            "`facebook.com_cookies.txt`\n"
            "`x.com_cookies.txt`\n\n"
            "Export with *Get cookies.txt LOCALLY* Chrome extension."
        )
        await q.message.edit_text(text, reply_markup=back_kb(), parse_mode="Markdown")
        return ST_MAIN

    # ── STATUS ──
    if data == "m_status":
        lines = ["*📊 Platform status:*"]
        for p in PLATFORMS:
            n  = len(load_profiles(uid, p))
            ck = "✅" if (cookie_dir(uid) / PLATFORMS[p][1]).exists() else "❌"
            lines.append(f"  {p.capitalize():<12} profiles: {n}  cookies: {ck}")
        ch = get_channel(uid)
        lines.append(f"\n📡 Output channel: *{ch or 'not set'}*")
        await q.message.edit_text("\n".join(lines), reply_markup=back_kb(), parse_mode="Markdown")
        return ST_MAIN

    # ── SET CHANNEL ──
    if data == "m_channel":
        ch = get_channel(uid)
        current = f"Current: *{ch}*" if ch else "Not set yet."
        ctx.user_data["awaiting"] = "channel"
        await q.message.edit_text(
            f"📡 *Set output channel*\n\n{current}\n\n"
            "Send channel username or ID:\n"
            "`@yourchannel` or `-100xxxxxxxxxx`\n\n"
            "Bot must be *admin* in that channel.\n"
            "Send `clear` to remove.",
            reply_markup=back_kb(), parse_mode="Markdown")
        return ST_SET_CHANNEL

    # ── PLATFORM → ADD ──
    if data.startswith("add_"):
        platform = data.split("_", 1)[1]
        ctx.user_data["add_platform"] = platform
        await q.message.edit_text(
            f"➕ *Add {platform.capitalize()} profile*\n\n"
            f"Send the profile URL:\n`{PLATFORM_URLS[platform]}username/`",
            reply_markup=back_kb(), parse_mode="Markdown")
        return ST_ADD_URL

    # ── PLATFORM → REMOVE LIST ──
    if data.startswith("rem_"):
        platform = data.split("_", 1)[1]
        urls = load_profiles(uid, platform)
        if not urls:
            await q.message.edit_text(
                f"No profiles for {platform}.", reply_markup=back_kb())
            return ST_MAIN
        rows = [[InlineKeyboardButton(
                    u.rstrip("/").split("/")[-1],
                    callback_data=f"del_{platform}|||{u}")] for u in urls]
        rows.append([InlineKeyboardButton("🔙 Back", callback_data="m_back")])
        await q.message.edit_text(
            f"🗑️ Tap to remove from *{platform}*:",
            reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown")
        return ST_REM_URL

    # ── DELETE URL ──
    if data.startswith("del_"):
        _, rest       = data.split("_", 1)
        platform, url = rest.split("|||", 1)
        urls = load_profiles(uid, platform)
        if url in urls:
            urls.remove(url); save_profiles(uid, platform, urls)
        await show_menu(q.message, uid, uname, name, edit=True)
        return ST_MAIN

    # ── STORY PLATFORM ──
    if data.startswith("story_"):
        platform = data.split("_", 1)[1]
        ctx.user_data["story_platform"] = platform
        await q.message.edit_text(
            f"📖 *Download {platform.capitalize()} Stories*\n\n"
            f"Send the profile URL:\n`{PLATFORM_URLS[platform]}username/`",
            reply_markup=back_kb(), parse_mode="Markdown")
        return ST_STORY_URL

    # ── HIGHLIGHT PLATFORM ──
    if data.startswith("highlight_"):
        platform = data.split("_", 1)[1]
        ctx.user_data["highlight_platform"] = platform
        await q.message.edit_text(
            f"🌟 *Download {platform.capitalize()} Highlights*\n\n"
            f"Send the profile URL:\n`{PLATFORM_URLS[platform]}username/`",
            reply_markup=back_kb(), parse_mode="Markdown")
        return ST_HIGHLIGHT_URL

    # ── DOWNLOAD TYPE CHOSEN ──
    if data.startswith("dl_"):
        choice = data.split("_")[1]
        STOP_EVENTS[uid] = asyncio.Event()
        asyncio.create_task(
            do_download(q.message, choice, uid, uname, name, ctx.bot))
        return ST_MAIN

    return ST_MAIN

# ── TEXT INPUT ────────────────────────────────────────────────────────────────
async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    uid     = user.id
    uname   = ctx.user_data.get("username", user.username or "user")
    name    = ctx.user_data.get("name", user.full_name)
    text    = update.message.text.strip()
    waiting = ctx.user_data.get("awaiting")

    # Channel
    if waiting == "channel":
        ctx.user_data.pop("awaiting", None)
        if text.lower() == "clear":
            set_channel(uid, None)
            await update.message.reply_text("📡 Channel removed.")
        else:
            set_channel(uid, text)
            await update.message.reply_text(
                f"📡 Channel set to *{text}*\nMake sure bot is admin there.",
                parse_mode="Markdown")
        await show_menu(update.message, uid, uname, name)
        return ST_MAIN

    # Story URL
    if ctx.user_data.get("story_platform"):
        platform = ctx.user_data.pop("story_platform")
        STOP_EVENTS[uid] = asyncio.Event()
        asyncio.create_task(
            do_special_download(update.message, text, platform,
                                "stories", uid, uname, name, ctx.bot))
        return ST_MAIN

    # Highlight URL
    if ctx.user_data.get("highlight_platform"):
        platform = ctx.user_data.pop("highlight_platform")
        STOP_EVENTS[uid] = asyncio.Event()
        asyncio.create_task(
            do_special_download(update.message, text, platform,
                                "highlights", uid, uname, name, ctx.bot))
        return ST_MAIN

    # Add profile URL
    platform = ctx.user_data.get("add_platform")
    if platform:
        ctx.user_data.pop("add_platform", None)
        urls = load_profiles(uid, platform)
        if text in urls:
            await update.message.reply_text("Already in list.")
        else:
            urls.append(text); save_profiles(uid, platform, urls)
            await update.message.reply_text(
                f"✅ Added to *{platform}*.", parse_mode="Markdown")
        await show_menu(update.message, uid, uname, name)
        return ST_MAIN

    return ST_MAIN

# ── COOKIE UPLOAD ─────────────────────────────────────────────────────────────
async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user  = update.effective_user
    uid   = user.id
    doc   = update.message.document
    fname = doc.file_name
    known = {PLATFORMS[p][1] for p in PLATFORMS}
    if fname not in known:
        await update.message.reply_text(
            f"⚠ Unknown: `{fname}`\nExpected:\n" + "\n".join(f"`{k}`" for k in sorted(known)),
            parse_mode="Markdown")
        return
    tg_file = await doc.get_file()
    await tg_file.download_to_drive(str(cookie_dir(uid) / fname))
    await update.message.reply_text(f"✅ Cookie saved: `{fname}`", parse_mode="Markdown")

# ── GALLERY-DL COMMAND BUILDER ────────────────────────────────────────────────
def build_cmd(out_dir, cookie, sleep, url, mode="photos"):
    cmd = ["gallery-dl", "-D", str(out_dir),
           "--download-archive", str(out_dir / "archive.txt"),
           "--sleep-request", str(sleep)]
    if mode == "photos":
        cmd += ["--filter", "extension in ('jpg','jpeg','png','gif','webp','bmp')"]
    elif mode == "videos":
        cmd += ["--filter", "extension in ('mp4','webm','mkv','mov','avi','m4v')"]
    # stories/highlights/documents: no filter — download everything
    if cookie.exists():
        cmd += ["--cookies", str(cookie)]
    cmd.append(url)
    return cmd

def stories_url(platform: str, profile_url: str) -> str:
    """Convert profile URL to stories URL for supported platforms."""
    user = profile_url.rstrip("/").split("/")[-1].lstrip("@")
    if platform == "instagram":
        return f"https://www.instagram.com/stories/{user}/"
    # TikTok/Facebook/X don't have a dedicated stories endpoint in gallery-dl
    return profile_url

# ── SEND HELPERS ──────────────────────────────────────────────────────────────
async def _do_send_group(target, group):
    """Send a media group. Requires 2–10 items."""
    if hasattr(target, "reply_media_group"):
        await target.reply_media_group(group)
    else:
        bot, cid = target
        await bot.send_media_group(chat_id=cid, media=group)

async def _do_send_single(target, f: Path, kind: str):
    """Send a single file as photo, video or document."""
    try:
        if kind == "photo":
            if hasattr(target, "reply_photo"):
                await target.reply_photo(photo=open(f, "rb"))
            else:
                bot, cid = target; await bot.send_photo(chat_id=cid, photo=open(f, "rb"))
        elif kind == "video":
            if hasattr(target, "reply_video"):
                await target.reply_video(video=open(f, "rb"))
            else:
                bot, cid = target; await bot.send_video(chat_id=cid, video=open(f, "rb"))
        else:
            if hasattr(target, "reply_document"):
                await target.reply_document(document=open(f, "rb"))
            else:
                bot, cid = target; await bot.send_document(chat_id=cid, document=open(f, "rb"))
    except Exception:
        pass

async def send_batch(target, batch: list[Path], send_as: str):
    """
    Send a batch of files.
    send_as: 'photos' | 'videos' | 'documents' | 'mixed'
    Telegram media_group requires exactly 2–10 items.
    """
    if not batch: return

    def classify(f):
        ext = f.suffix.lower()
        if ext in PHOTO_EXT: return "photo"
        if ext in VIDEO_EXT: return "video"
        return "doc"

    if send_as == "documents":
        for f in batch:
            await _do_send_single(target, f, "doc")
        return

    # Group into chunks of 10 for media_group
    for i in range(0, len(batch), 10):
        chunk = batch[i:i+10]
        if len(chunk) == 1:
            # Single file — send individually, no media_group needed
            kind = "photo" if send_as == "photos" else "video"
            if send_as == "mixed":
                kind = classify(chunk[0])
            await _do_send_single(target, chunk[0], kind)
            continue
        try:
            if send_as == "photos":
                group = [InputMediaPhoto(open(f, "rb")) for f in chunk]
            elif send_as == "videos":
                group = [InputMediaVideo(open(f, "rb")) for f in chunk]
            else:  # mixed
                group = []
                for f in chunk:
                    k = classify(f)
                    if k == "photo":
                        group.append(InputMediaPhoto(open(f, "rb")))
                    else:
                        group.append(InputMediaVideo(open(f, "rb")))
            await _do_send_group(target, group)
        except Exception:
            # Fallback: send each individually
            for f in chunk:
                k = classify(f)
                if send_as == "photos": k = "photo"
                elif send_as == "videos": k = "video"
                await _do_send_single(target, f, k)

# ── REALTIME DOWNLOAD ─────────────────────────────────────────────────────────
async def realtime_download(send_target, out_dir: Path, cookie: Path,
                             sleep: int, url: str, mode: str,
                             stop: asyncio.Event) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)

    # Determine which extensions to watch
    if mode == "photos":
        exts = PHOTO_EXT
        send_as = "photos"
    elif mode == "videos":
        exts = VIDEO_EXT
        send_as = "videos"
    elif mode == "documents":
        exts = PHOTO_EXT | VIDEO_EXT
        send_as = "documents"
    else:  # stories / highlights / mixed
        exts = PHOTO_EXT | VIDEO_EXT
        send_as = "mixed"

    cmd  = build_cmd(out_dir, cookie, sleep, url, mode)
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)

    seen: set[Path] = set()
    buf:  list[Path] = []
    sent = 0

    async def flush():
        nonlocal sent
        if not buf: return
        await send_batch(send_target, list(buf), send_as)
        sent += len(buf)
        buf.clear()

    while proc.returncode is None:
        if stop.is_set():
            proc.kill(); break
        await asyncio.sleep(3)          # 3s poll — gives gallery-dl time to batch
        if out_dir.exists():
            for f in sorted(out_dir.iterdir()):
                if f in seen or not f.is_file() or f.suffix.lower() not in exts:
                    continue
                seen.add(f)
                buf.append(f)
                if len(buf) == 10:
                    await flush()
        try:
            await asyncio.wait_for(proc.wait(), timeout=0.1)
        except asyncio.TimeoutError:
            pass

    # Final sweep after process exits
    await asyncio.sleep(1)
    if not stop.is_set() and out_dir.exists():
        for f in sorted(out_dir.iterdir()):
            if f in seen or not f.is_file() or f.suffix.lower() not in exts:
                continue
            seen.add(f)
            buf.append(f)
            if len(buf) == 10:
                await flush()
    await flush()
    return sent

# ── DOWNLOAD ORCHESTRATOR ─────────────────────────────────────────────────────
async def do_download(msg, choice: str, uid: int, uname: str, name: str, bot):
    stop = STOP_EVENTS.get(uid, asyncio.Event())
    mode_map = {"1": "photos", "2": "videos", "3": "both", "4": "documents"}
    mode  = mode_map.get(choice, "photos")
    label = {"photos": "📷 Photos", "videos": "🎬 Videos",
             "both": "📦 Both", "documents": "📁 Files"}[mode]

    channel     = get_channel(uid)
    send_target = (bot, channel) if channel else msg

    status = await msg.reply_text(
        f"⏳ *{label}* — starting…"
        + (f"\n📡 → {channel}" if channel else ""),
        parse_mode="Markdown")

    total = 0; start = datetime.now()

    for platform, (_, cfile, sleep) in PLATFORMS.items():
        if stop.is_set(): break
        urls = load_profiles(uid, platform)
        if not urls: continue
        for url in urls:
            if stop.is_set(): break
            user_handle = url.rstrip("/").split("/")[-1].lstrip("@")
            try:
                await status.edit_text(
                    f"⏳ *{platform.capitalize()}* › `{user_handle}`",
                    parse_mode="Markdown")
            except Exception:
                pass

            modes = ["photos", "videos"] if mode == "both" else [mode]
            for m in modes:
                if stop.is_set(): break
                out_dir = (DATA_ROOT / str(uid) / "downloads"
                           / platform.capitalize() / user_handle / m.capitalize())
                n = await realtime_download(
                    send_target, out_dir, cookie_dir(uid) / cfile, sleep, url, m, stop)
                total += n
                if n > 0:
                    save_history(uid, {
                        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "platform": platform, "user": user_handle,
                        "media": m, "sent": n,
                    })

    elapsed = (datetime.now() - start).seconds
    final   = (f"⏹️ *Stopped.* {total} file(s) in {elapsed}s."
               if stop.is_set() else
               f"✅ *Done!* {total} file(s) in {elapsed}s.")
    try:
        await status.edit_text(final, parse_mode="Markdown")
    except Exception:
        await msg.reply_text(final, parse_mode="Markdown")

    STOP_EVENTS.pop(uid, None)
    await show_menu(msg, uid, uname, name)

# ── SPECIAL DOWNLOAD (stories / highlights) ───────────────────────────────────
async def do_special_download(msg, url: str, platform: str,
                               mode: str, uid: int, uname: str, name: str, bot):
    stop        = STOP_EVENTS.get(uid, asyncio.Event())
    label       = "📖 Stories" if mode == "stories" else "🌟 Highlights"
    channel     = get_channel(uid)
    send_target = (bot, channel) if channel else msg
    user_handle = url.rstrip("/").split("/")[-1].lstrip("@")

    status = await msg.reply_text(
        f"⏳ *{label}* › `{user_handle}`…", parse_mode="Markdown")

    _, cfile, sleep = PLATFORMS[platform]
    out_dir = (DATA_ROOT / str(uid) / "downloads"
               / platform.capitalize() / user_handle / mode.capitalize())

    n = await realtime_download(
        send_target, out_dir, cookie_dir(uid) / cfile, sleep, url, mode, stop)

    save_history(uid, {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "platform": platform, "user": user_handle,
        "media": mode, "sent": n,
    })

    try:
        await status.edit_text(f"✅ *Done!* {n} file(s) sent.", parse_mode="Markdown")
    except Exception:
        pass

    STOP_EVENTS.pop(uid, None)
    await show_menu(msg, uid, uname, name)

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    COOKIES_ROOT.mkdir(parents=True, exist_ok=True)

    app = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            CommandHandler("menu",  cmd_start),
        ],
        states={
            ST_MAIN:          [CallbackQueryHandler(cb_router)],
            ST_ADD_PLAT:      [CallbackQueryHandler(cb_router)],
            ST_ADD_URL:       [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
                               CallbackQueryHandler(cb_router)],
            ST_REM_PLAT:      [CallbackQueryHandler(cb_router)],
            ST_REM_URL:       [CallbackQueryHandler(cb_router)],
            ST_MEDIA_CHOICE:  [CallbackQueryHandler(cb_router)],
            ST_SET_CHANNEL:   [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
                               CallbackQueryHandler(cb_router)],
            ST_STORY_PLAT:    [CallbackQueryHandler(cb_router)],
            ST_HIGHLIGHT_PLAT:[CallbackQueryHandler(cb_router)],
            ST_STORY_URL:     [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
                               CallbackQueryHandler(cb_router)],
            ST_HIGHLIGHT_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
                               CallbackQueryHandler(cb_router)],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
        per_message=False,
        per_user=True,
        per_chat=False,
    )

    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    print("Cuhi Bot running…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
