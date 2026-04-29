#!/usr/bin/env python3
"""
Cuhi Bot — Media Downloader
- No ConversationHandler (fixes all back button issues)
- Instant stop via asyncio.Event + proc.kill()
- Per-user isolated data
- Real-time grouped delivery (albums sent as they arrive)
- Smart archive cleanup (mirrors download.sh logic)
- Global cookies loaded from Railway env vars at startup
"""

import asyncio, json, os
from datetime import datetime
from telegram.error import RetryAfter
from pathlib import Path
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    InputMediaPhoto, InputMediaVideo,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)

# ── CONFIG ────────────────────────────────────────────────────────────────────

TOKEN        = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
DATA_ROOT    = Path("./data")
COOKIES_ROOT = Path("./cookies")

GLOBAL_COOKIES_DIR = COOKIES_ROOT / "global"

PLATFORMS = {
    "instagram": ("instagram_profiles.txt", "instagram.com_cookies.txt", 1),
    "tiktok":    ("tiktok_profiles.txt",    "tiktok.com_cookies.txt",    1),
    "facebook":  ("facebook_profiles.txt",  "facebook.com_cookies.txt",  1),
    "x":         ("x_profiles.txt",         "x.com_cookies.txt",         1),
}

COOKIE_ENV_VARS = {
    "instagram": "COOKIE_INSTAGRAM",
    "tiktok":    "COOKIE_TIKTOK",
    "facebook":  "COOKIE_FACEBOOK",
    "x":         "COOKIE_X",
}

PLATFORM_URLS = {
    "instagram": "https://www.instagram.com/",
    "tiktok":    "https://www.tiktok.com/@",
    "facebook":  "https://www.facebook.com/",
    "x":         "https://x.com/",
}

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXT = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v"}

# Telegram album max size
ALBUM_MAX = 10

# Global per-user stop events
STOP_EVENTS: dict[int, asyncio.Event] = {}

# ── TELEGRAM FLOOD-CONTROL WRAPPER ───────────────────────────────────────────

async def safe_api(fn, *args, **kwargs):
    """
    Retries on flood control (RetryAfter). Swallows all other exceptions
    so a single failed send never crashes the whole download task.
    """
    while True:
        try:
            return await fn(*args, **kwargs)
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except Exception:
            return None

# ── COOKIE BOOTSTRAP FROM ENV ─────────────────────────────────────────────────

def load_cookies_from_env():
    GLOBAL_COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    loaded = []
    for platform, env_var in COOKIE_ENV_VARS.items():
        content = os.environ.get(env_var, "").strip()
        if not content:
            continue
        cookie_filename = PLATFORMS[platform][1]
        dest = GLOBAL_COOKIES_DIR / cookie_filename
        dest.write_text(content)
        loaded.append(platform)
    if loaded:
        print(f"[cookies] Loaded from env vars: {', '.join(loaded)}")
    else:
        print("[cookies] No cookie env vars found — users must upload cookies manually.")

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

def get_cookie_path(uid, cfile: str) -> Path:
    user_cookie = cookie_dir(uid) / cfile
    if user_cookie.exists():
        return user_cookie
    return GLOBAL_COOKIES_DIR / cfile

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

def set_channel(uid, val):
    s = load_settings(uid)
    if val: s["channel"] = val
    else:   s.pop("channel", None)
    save_settings(uid, s)

def source_count(uid):
    return str(sum(len(load_profiles(uid, p)) for p in PLATFORMS))

def cookie_status(uid):
    ok = []
    for p in PLATFORMS:
        cfile = PLATFORMS[p][1]
        user_ck   = cookie_dir(uid) / cfile
        global_ck = GLOBAL_COOKIES_DIR / cfile
        if user_ck.exists():
            ok.append(f"{p}(own)")
        elif global_ck.exists():
            ok.append(f"{p}(global)")
    return ", ".join(ok) if ok else "none"

def total_sent(uid):
    return sum(e.get("sent", 0) for e in load_history(uid))

# ── ARCHIVE CLEANUP (mirrors download.sh logic) ───────────────────────────────

def check_and_clean_archive(folder: Path):
    """
    Mirrors the check_and_clean_archive logic from download.sh:
      - folder has 0 media files  → delete archive (so gallery-dl re-downloads)
      - files on disk < archive entries → stale archive, delete it
      - otherwise → archive valid, leave it alone
    Called before each gallery-dl run so re-runs after manual file deletion work correctly.
    """
    archive = folder / "archive.txt"
    media_exts = PHOTO_EXT | VIDEO_EXT

    if not folder.exists():
        return

    file_count = sum(
        1 for f in folder.iterdir()
        if f.is_file() and f.name != "archive.txt" and f.suffix.lower() in media_exts
    )

    arch_count = 0
    if archive.exists():
        try:
            arch_count = sum(1 for line in archive.read_text().splitlines() if line.strip())
        except Exception:
            arch_count = 0

    if file_count == 0:
        if archive.exists():
            archive.unlink()
    elif file_count < arch_count:
        archive.unlink()
    # else: archive valid, do nothing

# ── USER STATE ────────────────────────────────────────────────────────────────

STATE_MAIN          = "main"
STATE_ADD_URL       = "add_url"
STATE_SET_CHANNEL   = "set_channel"
STATE_STORY_URL     = "story_url"
STATE_HIGHLIGHT_URL = "highlight_url"

def get_state(ctx): return ctx.user_data.get("state", STATE_MAIN)
def set_state(ctx, s): ctx.user_data["state"] = s

# ── MENU TEXT ─────────────────────────────────────────────────────────────────

def menu_text(uid, username, name):
    ch = get_channel(uid)
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
        "🕵️‍♂️ Moderation and butler modes\n"
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
        f" 🗂 Sources : *{source_count(uid)}*\n"
        f" 🍪 Cookies : *{cookie_status(uid)}*"
        f"{ch_line}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "👨‍💻 Developer: @copyrightpost"
    )

# ── KEYBOARDS ─────────────────────────────────────────────────────────────────

def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add source",     callback_data="m_add"),
         InlineKeyboardButton("🚫 Remove source",  callback_data="m_remove")],
        [InlineKeyboardButton("🌐 My sources",     callback_data="m_list"),
         InlineKeyboardButton("✅ Run download",   callback_data="m_run")],
        [InlineKeyboardButton("📖 Stories",        callback_data="m_stories"),
         InlineKeyboardButton("✨ Highlights",     callback_data="m_highlights")],
        [InlineKeyboardButton("🚫 Stop download",  callback_data="m_stop"),
         InlineKeyboardButton("📜 History",        callback_data="m_history")],
        [InlineKeyboardButton("🍪 Set cookies",    callback_data="m_cookies"),
         InlineKeyboardButton("📊 Status",         callback_data="m_status")],
        [InlineKeyboardButton("📡 Set channel",    callback_data="m_channel")],
    ])

def back_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="m_back")]])

def platform_kb(prefix):
    rows = [[InlineKeyboardButton(p.capitalize(), callback_data=f"{prefix}_{p}")]
            for p in PLATFORMS]
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="m_back")])
    return InlineKeyboardMarkup(rows)

def media_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼️ Photos only",       callback_data="dl_1")],
        [InlineKeyboardButton("🎬 Videos only",        callback_data="dl_2")],
        [InlineKeyboardButton("🔖 Both (separately)",  callback_data="dl_3")],
        [InlineKeyboardButton("📁 Files (as docs)",    callback_data="dl_4")],
        [InlineKeyboardButton("🔙 Back",               callback_data="m_back")],
    ])

# ── SHOW MENU ─────────────────────────────────────────────────────────────────

async def show_menu(msg, uid, username, name, edit=False):
    text = menu_text(uid, username, name)
    try:
        if edit:
            await safe_api(msg.edit_text, text, reply_markup=main_menu_kb(), parse_mode="Markdown")
        else:
            await safe_api(msg.reply_text, text, reply_markup=main_menu_kb(), parse_mode="Markdown")
    except Exception:
        try:
            await safe_api(
                msg.reply_text,
                f"@{username} | ✅ Sent: *{total_sent(uid)}* | 🗂 Sources: *{source_count(uid)}*",
                reply_markup=main_menu_kb(), parse_mode="Markdown")
        except Exception:
            pass

# ── /start & /menu ────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid  = user.id
    user_dir(uid)
    ctx.user_data["username"] = user.username or "user"
    ctx.user_data["name"]     = user.full_name
    set_state(ctx, STATE_MAIN)
    await show_menu(update.effective_message, uid,
                    ctx.user_data["username"], ctx.user_data["name"])

# ── CALLBACK HANDLER ──────────────────────────────────────────────────────────

async def cb_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    data = q.data
    user = q.from_user
    uid  = user.id
    uname = ctx.user_data.get("username", user.username or "user")
    name  = ctx.user_data.get("name", user.full_name)
    await q.answer()

    if data == "m_back":
        set_state(ctx, STATE_MAIN)
        ctx.user_data.pop("add_platform", None)
        ctx.user_data.pop("story_platform", None)
        ctx.user_data.pop("highlight_platform", None)
        await show_menu(q.message, uid, uname, name, edit=True)
        return

    if data == "m_add":
        await q.message.edit_text(
            "➕ *Add source* — Choose platform:",
            reply_markup=platform_kb("add"), parse_mode="Markdown")
        return

    if data == "m_remove":
        await q.message.edit_text(
            "🗑️ *Remove source* — Choose platform:",
            reply_markup=platform_kb("rem"), parse_mode="Markdown")
        return

    if data == "m_list":
        lines = []
        for p in PLATFORMS:
            urls = load_profiles(uid, p)
            if urls:
                lines.append(f"*{p.upper()}*")
                lines += [f"  • `{u}`" for u in urls]
        text = "\n".join(lines) if lines else "No sources added yet."
        await q.message.edit_text(text, reply_markup=back_kb(), parse_mode="Markdown")
        return

    if data == "m_run":
        if not any(load_profiles(uid, p) for p in PLATFORMS):
            await q.message.edit_text("⚠️ No sources yet. Add one first.", reply_markup=back_kb())
            return
        await q.message.edit_text(
            "🚀 *What to download?*\n\n"
            "🖼️ *Photos only* — sends as images\n"
            "🎬 *Videos only* — sends as videos\n"
            "📦 *Both* — photos then videos\n"
            "📁 *Files* — sends as documents",
            reply_markup=media_kb(), parse_mode="Markdown")
        return

    if data == "m_stories":
        await q.message.edit_text(
            "📖 *Download Stories* — Choose platform:",
            reply_markup=platform_kb("story"), parse_mode="Markdown")
        return

    if data == "m_highlights":
        await q.message.edit_text(
            "🌟 *Download Highlights* — Choose platform:",
            reply_markup=platform_kb("highlight"), parse_mode="Markdown")
        return

    if data == "m_stop":
        ev = STOP_EVENTS.get(uid)
        if ev and not ev.is_set():
            ev.set()
            await q.message.edit_text(
                "⏹️ *Stop signal sent.* Download will halt immediately.",
                reply_markup=back_kb(), parse_mode="Markdown")
        else:
            await q.message.edit_text("Nothing is running.", reply_markup=back_kb())
        return

    if data == "m_history":
        h = load_history(uid)
        if not h:
            text = "No history yet."
        else:
            rows = [f"• {e['date']} {e['platform']} `{e['user']}` {e['sent']} files"
                    for e in h[:10]]
            text = "*📜 Last 10 downloads:*\n" + "\n".join(rows)
        await q.message.edit_text(text, reply_markup=back_kb(), parse_mode="Markdown")
        return

    if data == "m_cookies":
        status_lines = []
        for p in PLATFORMS:
            cfile = PLATFORMS[p][1]
            user_ck   = cookie_dir(uid) / cfile
            global_ck = GLOBAL_COOKIES_DIR / cfile
            if user_ck.exists():
                status_lines.append(f"  ✅ `{cfile}` _(your upload)_")
            elif global_ck.exists():
                status_lines.append(f"  🌐 `{cfile}` _(global / Railway var)_")
            else:
                status_lines.append(f"  ❌ `{cfile}`")
        status_block = "\n".join(status_lines)
        await q.message.edit_text(
            "🍪 *Cookie status:*\n"
            f"{status_block}\n\n"
            "*To upload your own cookie* (overrides global):\n"
            "Send the file named exactly:\n"
            "`instagram.com_cookies.txt`\n"
            "`tiktok.com_cookies.txt`\n"
            "`facebook.com_cookies.txt`\n"
            "`x.com_cookies.txt`\n\n"
            "Export with *Get cookies.txt LOCALLY* Chrome extension.\n\n"
            "*To update global cookies*, edit the Railway variable "
            "(`COOKIE_INSTAGRAM`, `COOKIE_TIKTOK`, `COOKIE_FACEBOOK`, `COOKIE_X`) "
            "and redeploy.",
            reply_markup=back_kb(), parse_mode="Markdown")
        return

    if data == "m_status":
        lines = ["*📊 Platform status:*"]
        for p in PLATFORMS:
            n     = len(load_profiles(uid, p))
            cfile = PLATFORMS[p][1]
            if (cookie_dir(uid) / cfile).exists():
                ck = "✅ own"
            elif (GLOBAL_COOKIES_DIR / cfile).exists():
                ck = "🌐 global"
            else:
                ck = "❌ none"
            lines.append(f"  {p.capitalize():<12} profiles: {n}  cookies: {ck}")
        ch = get_channel(uid)
        lines.append(f"\n📡 Channel: *{ch or 'not set'}*")
        await q.message.edit_text("\n".join(lines), reply_markup=back_kb(), parse_mode="Markdown")
        return

    if data == "m_channel":
        ch = get_channel(uid)
        set_state(ctx, STATE_SET_CHANNEL)
        await q.message.edit_text(
            f"📡 *Set output channel*\n\n"
            f"Current: *{ch or 'not set'}*\n\n"
            "Send channel username:\n`@yourchannel`\n\n"
            "Or send `clear` to remove.\n"
            "Bot must be *admin* in the channel.",
            reply_markup=back_kb(), parse_mode="Markdown")
        return

    if data.startswith("add_"):
        platform = data.split("_", 1)[1]
        ctx.user_data["add_platform"] = platform
        set_state(ctx, STATE_ADD_URL)
        await q.message.edit_text(
            f"➕ *Add {platform.capitalize()} profile*\n\n"
            f"Send the profile URL:\n`{PLATFORM_URLS[platform]}username/`",
            reply_markup=back_kb(), parse_mode="Markdown")
        return

    if data.startswith("rem_"):
        platform = data.split("_", 1)[1]
        urls = load_profiles(uid, platform)
        if not urls:
            await q.message.edit_text(f"No profiles for {platform}.", reply_markup=back_kb())
            return
        rows = [[InlineKeyboardButton(
            u.rstrip("/").split("/")[-1],
            callback_data=f"del_{platform}|||{u}")] for u in urls]
        rows.append([InlineKeyboardButton("🔙 Back", callback_data="m_back")])
        await q.message.edit_text(
            f"🗑️ Tap profile to remove from *{platform}*:",
            reply_markup=InlineKeyboardMarkup(rows), parse_mode="Markdown")
        return

    if data.startswith("del_"):
        _, rest = data.split("_", 1)
        platform, url = rest.split("|||", 1)
        urls = load_profiles(uid, platform)
        if url in urls:
            urls.remove(url); save_profiles(uid, platform, urls)
        await show_menu(q.message, uid, uname, name, edit=True)
        return

    if data.startswith("story_"):
        platform = data.split("_", 1)[1]
        ctx.user_data["story_platform"] = platform
        set_state(ctx, STATE_STORY_URL)
        await q.message.edit_text(
            f"📖 *{platform.capitalize()} Stories*\n\n"
            f"Send the profile URL:\n`{PLATFORM_URLS[platform]}username/`",
            reply_markup=back_kb(), parse_mode="Markdown")
        return

    if data.startswith("highlight_"):
        platform = data.split("_", 1)[1]
        ctx.user_data["highlight_platform"] = platform
        set_state(ctx, STATE_HIGHLIGHT_URL)
        await q.message.edit_text(
            f"🌟 *{platform.capitalize()} Highlights*\n\n"
            f"Send the profile URL:\n`{PLATFORM_URLS[platform]}username/`",
            reply_markup=back_kb(), parse_mode="Markdown")
        return

    if data.startswith("dl_"):
        choice = data.split("_")[1]
        ev = asyncio.Event()
        STOP_EVENTS[uid] = ev
        asyncio.create_task(
            do_download(q.message, choice, uid, uname, name, ctx.bot, ev))
        return

# ── TEXT HANDLER ──────────────────────────────────────────────────────────────

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user  = update.effective_user
    uid   = user.id
    uname = ctx.user_data.get("username", user.username or "user")
    name  = ctx.user_data.get("name", user.full_name)
    text  = update.message.text.strip()
    state = get_state(ctx)

    if state == STATE_SET_CHANNEL:
        set_state(ctx, STATE_MAIN)
        if text.lower() == "clear":
            set_channel(uid, None)
            await update.message.reply_text("📡 Channel removed.")
        else:
            set_channel(uid, text)
            await update.message.reply_text(
                f"📡 Channel set to *{text}*\nMake sure bot is admin there.",
                parse_mode="Markdown")
        await show_menu(update.message, uid, uname, name)
        return

    if state == STATE_ADD_URL:
        platform = ctx.user_data.pop("add_platform", None)
        if platform:
            urls = load_profiles(uid, platform)
            if text in urls:
                await update.message.reply_text("Already in list.")
            else:
                urls.append(text); save_profiles(uid, platform, urls)
                await update.message.reply_text(
                    f"✅ Added to *{platform}*.", parse_mode="Markdown")
        set_state(ctx, STATE_MAIN)
        await show_menu(update.message, uid, uname, name)
        return

    if state == STATE_STORY_URL:
        platform = ctx.user_data.pop("story_platform", None)
        if platform:
            ev = asyncio.Event()
            STOP_EVENTS[uid] = ev
            asyncio.create_task(
                do_special_download(update.message, text, platform,
                                    "stories", uid, uname, name, ctx.bot, ev))
        set_state(ctx, STATE_MAIN)
        return

    if state == STATE_HIGHLIGHT_URL:
        platform = ctx.user_data.pop("highlight_platform", None)
        if platform:
            ev = asyncio.Event()
            STOP_EVENTS[uid] = ev
            asyncio.create_task(
                do_special_download(update.message, text, platform,
                                    "highlights", uid, uname, name, ctx.bot, ev))
        set_state(ctx, STATE_MAIN)
        return

# ── COOKIE UPLOAD ─────────────────────────────────────────────────────────────

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid   = update.effective_user.id
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
    await update.message.reply_text(
        f"✅ Cookie saved: `{fname}` _(overrides global cookie for your account)_",
        parse_mode="Markdown")

# ── GALLERY-DL CMD ────────────────────────────────────────────────────────────

def build_cmd(out_dir: Path, cookie: Path, sleep: int,
              url: str, mode: str) -> list[str]:
    cmd = ["gallery-dl",
           "-D", str(out_dir),
           "--download-archive", str(out_dir / "archive.txt"),
           "--sleep-request", str(sleep),
           "--http-timeout", "30"]
    if mode == "photos":
        cmd += ["--filter",
                "extension in ('jpg','jpeg','png','gif','webp','bmp')"]
    elif mode == "videos":
        cmd += ["--filter",
                "extension in ('mp4','webm','mkv','mov','avi','m4v')"]
    elif mode == "stories":
        if "instagram.com" in url:
            user = url.rstrip("/").split("/")[-1].lstrip("@")
            url  = f"https://www.instagram.com/stories/{user}/"
    elif mode == "highlights":
        pass
    if cookie.exists():
        cmd += ["--cookies", str(cookie)]
    cmd.append(url)
    return cmd

# ── SEND HELPERS ──────────────────────────────────────────────────────────────

def classify(f: Path) -> str:
    return "photo" if f.suffix.lower() in PHOTO_EXT else "video"

async def _send_single(target, f: Path, kind: str):
    try:
        if kind == "photo":
            if hasattr(target, "reply_photo"):
                await safe_api(target.reply_photo, photo=open(f, "rb"))
            else:
                bot, cid = target
                await safe_api(bot.send_photo, chat_id=cid, photo=open(f, "rb"))
        elif kind == "video":
            if hasattr(target, "reply_video"):
                await safe_api(target.reply_video, video=open(f, "rb"))
            else:
                bot, cid = target
                await safe_api(bot.send_video, chat_id=cid, video=open(f, "rb"))
        else:
            if hasattr(target, "reply_document"):
                await safe_api(target.reply_document, document=open(f, "rb"))
            else:
                bot, cid = target
                await safe_api(bot.send_document, chat_id=cid, document=open(f, "rb"))
    except Exception:
        pass

async def _send_group(target, group: list):
    if hasattr(target, "reply_media_group"):
        await safe_api(target.reply_media_group, group)
    else:
        bot, cid = target
        await safe_api(bot.send_media_group, chat_id=cid, media=group)

async def flush_album(target, album: list[Path], send_as: str):
    """Send a list of files as a Telegram album (media group) if >1, else single."""
    if not album:
        return
    if send_as == "documents":
        for f in album:
            await _send_single(target, f, "doc")
        return
    if len(album) == 1:
        kind = "photo" if send_as == "photos" else \
               "video" if send_as == "videos" else classify(album[0])
        await _send_single(target, album[0], kind)
        return
    try:
        group = []
        for f in album:
            if send_as == "photos":
                group.append(InputMediaPhoto(open(f, "rb")))
            elif send_as == "videos":
                group.append(InputMediaVideo(open(f, "rb")))
            else:
                if classify(f) == "photo":
                    group.append(InputMediaPhoto(open(f, "rb")))
                else:
                    group.append(InputMediaVideo(open(f, "rb")))
        await _send_group(target, group)
    except Exception:
        # Fallback: send individually if album fails
        for f in album:
            kind = "photo" if send_as == "photos" else \
                   "video" if send_as == "videos" else classify(f)
            await _send_single(target, f, kind)

# ── REALTIME DOWNLOAD ─────────────────────────────────────────────────────────

async def realtime_download(target, out_dir: Path, cookie: Path,
                            sleep: int, url: str, mode: str,
                            stop: asyncio.Event) -> int:
    """
    Runs gallery-dl and watches the output folder in real time.

    Grouping strategy:
      - Files are added to an album as they appear on disk.
      - Album is flushed (sent) immediately when it hits ALBUM_MAX (10).
      - Album is also flushed when no new file has appeared for ~1 second,
        giving real-time grouped delivery without waiting for a full 10.

    Stop strategy:
      - stop.is_set() is checked every 0.2s loop tick.
      - proc.kill() is called immediately — halts within one tick (~0.2s).
      - The stability check no longer uses asyncio.sleep so it never
        blocks the stop check.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # Archive cleanup before each run (mirrors download.sh logic)
    check_and_clean_archive(out_dir)

    if mode == "photos":
        exts    = PHOTO_EXT
        send_as = "photos"
    elif mode == "videos":
        exts    = VIDEO_EXT
        send_as = "videos"
    elif mode == "documents":
        exts    = PHOTO_EXT | VIDEO_EXT
        send_as = "documents"
    else:
        exts    = PHOTO_EXT | VIDEO_EXT
        send_as = "mixed"

    cmd  = build_cmd(out_dir, cookie, sleep, url, mode)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL)

    seen:  set[Path]  = set()
    album: list[Path] = []
    sent = 0
    last_new_file_time = asyncio.get_event_loop().time()

    async def flush_now():
        nonlocal sent
        if not album:
            return
        await flush_album(target, list(album), send_as)
        sent += len(album)
        album.clear()

    while True:
        # ── STOP CHECK — highest priority, checked before and after sleep ──
        if stop.is_set():
            try: proc.kill()
            except Exception: pass
            break

        await asyncio.sleep(0.2)

        if stop.is_set():
            try: proc.kill()
            except Exception: pass
            break

        now = asyncio.get_event_loop().time()

        # ── SCAN for new stable files ──────────────────────────────────────
        if out_dir.exists():
            for f in sorted(out_dir.iterdir()):
                if f in seen or not f.is_file():
                    continue
                if f.suffix.lower() not in exts:
                    continue
                if f.name == "archive.txt":
                    continue
                # Stability check: two immediate stat() reads — no sleep needed.
                # If sizes differ the file is still being written; revisit next tick.
                try:
                    s1 = f.stat().st_size
                    s2 = f.stat().st_size
                    if s1 == 0 or s1 != s2:
                        continue
                except Exception:
                    continue
                seen.add(f)
                album.append(f)
                last_new_file_time = now
                # Flush immediately if album is full
                if len(album) >= ALBUM_MAX:
                    await flush_now()

        # ── TIME-BASED FLUSH: send current album if quiet for ~1s ─────────
        # This delivers albums in real time even when < ALBUM_MAX files
        # have arrived — no more waiting for a batch of 10.
        if album and (now - last_new_file_time) >= 1.0:
            await flush_now()

        # ── CHECK if process finished ──────────────────────────────────────
        if proc.returncode is not None:
            break

        try:
            await asyncio.wait_for(asyncio.shield(proc.wait()), timeout=0.1)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    # ── FINAL SWEEP — catch any files written in the last moments ──────────
    if not stop.is_set() and out_dir.exists():
        await asyncio.sleep(0.3)
        for f in sorted(out_dir.iterdir()):
            if f in seen or not f.is_file(): continue
            if f.suffix.lower() not in exts:  continue
            if f.name == "archive.txt":       continue
            seen.add(f)
            album.append(f)
            if len(album) >= ALBUM_MAX:
                await flush_now()

    await flush_now()
    return sent

# ── DOWNLOAD ORCHESTRATOR ─────────────────────────────────────────────────────

async def do_download(msg, choice: str, uid: int,
                      uname: str, name: str, bot, stop: asyncio.Event):
    mode_map = {"1": "photos", "2": "videos", "3": "both", "4": "documents"}
    mode     = mode_map.get(choice, "photos")
    label    = {"photos": "🖼️ Photos", "videos": "🎬 Videos",
                "both":   "📦 Both",   "documents": "📁 Files"}[mode]
    channel     = get_channel(uid)
    send_target = (bot, channel) if channel else msg

    status = await safe_api(
        msg.reply_text,
        f"⏳ *{label}* — starting…" + (f"\n📡 → {channel}" if channel else ""),
        parse_mode="Markdown")
    total = 0
    start = datetime.now()

    for platform, (_, cfile, sleep) in PLATFORMS.items():
        if stop.is_set(): break
        urls = load_profiles(uid, platform)
        if not urls: continue

        cookie = get_cookie_path(uid, cfile)

        for url in urls:
            if stop.is_set(): break
            user_handle = url.rstrip("/").split("/")[-1].lstrip("@")
            await safe_api(
                status.edit_text,
                f"⏳ *{platform.capitalize()}* › `{user_handle}`",
                parse_mode="Markdown")
            modes = ["photos", "videos"] if mode == "both" else [mode]
            for m in modes:
                if stop.is_set(): break
                out_dir = (DATA_ROOT / str(uid) / "downloads"
                           / platform.capitalize() / user_handle / m.capitalize())
                n = await realtime_download(
                    send_target, out_dir, cookie, sleep, url, m, stop)
                total += n
                if n > 0:
                    save_history(uid, {
                        "date":     datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "platform": platform,
                        "user":     user_handle,
                        "media":    m,
                        "sent":     n,
                    })

    elapsed = int((datetime.now() - start).total_seconds())
    final   = (f"⏹️ *Stopped.* {total} file(s) in {elapsed}s."
               if stop.is_set() else
               f"✅ *Done!* {total} file(s) in {elapsed}s.")
    if not await safe_api(status.edit_text, final, parse_mode="Markdown"):
        await safe_api(msg.reply_text, final, parse_mode="Markdown")

    STOP_EVENTS.pop(uid, None)
    await show_menu(msg, uid, uname, name)

# ── SPECIAL DOWNLOAD (stories / highlights) ───────────────────────────────────

async def do_special_download(msg, url: str, platform: str,
                              mode: str, uid: int, uname: str,
                              name: str, bot, stop: asyncio.Event):
    label       = "📖 Stories" if mode == "stories" else "🌟 Highlights"
    channel     = get_channel(uid)
    send_target = (bot, channel) if channel else msg
    user_handle = url.rstrip("/").split("/")[-1].lstrip("@")
    status = await safe_api(
        msg.reply_text,
        f"⏳ *{label}* › `{user_handle}`…", parse_mode="Markdown")

    _, cfile, sleep = PLATFORMS[platform]
    cookie = get_cookie_path(uid, cfile)

    out_dir = (DATA_ROOT / str(uid) / "downloads"
               / platform.capitalize() / user_handle / mode.capitalize())
    n = await realtime_download(
        send_target, out_dir, cookie, sleep, url, mode, stop)

    if n > 0:
        save_history(uid, {
            "date":     datetime.now().strftime("%Y-%m-%d %H:%M"),
            "platform": platform,
            "user":     user_handle,
            "media":    mode,
            "sent":     n,
        })
    await safe_api(status.edit_text,
                   f"✅ *Done!* {n} file(s) sent.", parse_mode="Markdown")

    STOP_EVENTS.pop(uid, None)
    await show_menu(msg, uid, uname, name)

# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    COOKIES_ROOT.mkdir(parents=True, exist_ok=True)
    load_cookies_from_env()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu",  cmd_start))
    app.add_handler(CallbackQueryHandler(cb_router))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    print("Cuhi Bot running…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
