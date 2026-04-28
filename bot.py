#!/usr/bin/env python3
"""
Media Downloader Bot
- Public multi-user
- Works in private, groups, channels
- Per-user isolated data
- Album sending, real-time, stop/resume, history
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
TOKEN      = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
ADMIN_IDS  = {7232714487}        # can see all users' stats (optional)
DATA_ROOT  = Path("./data")      # data/{user_id}/...
COOKIES_ROOT = Path("./cookies") # cookies/{user_id}/...

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

# per-user stop events  {user_id: asyncio.Event}
STOP_EVENTS: dict[int, asyncio.Event] = {}

(ST_MAIN, ST_ADD_PLAT, ST_ADD_URL,
 ST_REM_PLAT, ST_REM_URL, ST_MEDIA_CHOICE,
 ST_SET_CHANNEL) = range(7)

# ── PER-USER PATHS ────────────────────────────────────────────────────────────
def user_dir(uid: int) -> Path:
    p = DATA_ROOT / str(uid)
    p.mkdir(parents=True, exist_ok=True)
    return p

def cookie_dir(uid: int) -> Path:
    p = COOKIES_ROOT / str(uid)
    p.mkdir(parents=True, exist_ok=True)
    return p

def profile_file(uid: int, platform: str) -> Path:
    return user_dir(uid) / PLATFORMS[platform][0]

def history_file(uid: int) -> Path:
    return user_dir(uid) / "history.json"

def settings_file(uid: int) -> Path:
    return user_dir(uid) / "settings.json"

# ── DATA HELPERS ──────────────────────────────────────────────────────────────
def load_profiles(uid: int, p: str) -> list[str]:
    f = profile_file(uid, p)
    return [l.strip() for l in f.read_text().splitlines() if l.strip()] if f.exists() else []

def save_profiles(uid: int, p: str, urls: list[str]):
    profile_file(uid, p).write_text("\n".join(urls) + "\n")

def load_history(uid: int) -> list[dict]:
    f = history_file(uid)
    return json.loads(f.read_text()) if f.exists() else []

def save_history(uid: int, entry: dict):
    h = load_history(uid)
    h.insert(0, entry)
    history_file(uid).write_text(json.dumps(h[:50], indent=2))

def load_settings(uid: int) -> dict:
    f = settings_file(uid)
    return json.loads(f.read_text()) if f.exists() else {}

def save_settings(uid: int, data: dict):
    settings_file(uid).write_text(json.dumps(data, indent=2))

def get_channel(uid: int) -> str | None:
    return load_settings(uid).get("channel")

def set_channel(uid: int, channel: str | None):
    s = load_settings(uid)
    if channel:
        s["channel"] = channel
    else:
        s.pop("channel", None)
    save_settings(uid, s)

def source_count(uid: int) -> str:
    return str(sum(len(load_profiles(uid, p)) for p in PLATFORMS))

def cookie_status(uid: int) -> str:
    ok = [p for p in PLATFORMS if (cookie_dir(uid) / PLATFORMS[p][1]).exists()]
    return ", ".join(ok) if ok else "none"

def total_sent(uid: int) -> int:
    return sum(e.get("sent", 0) for e in load_history(uid))

# ── KEYBOARDS ─────────────────────────────────────────────────────────────────
def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add source",     callback_data="m_add"),
         InlineKeyboardButton("🗑️ Remove source", callback_data="m_remove")],
        [InlineKeyboardButton("📋 My sources",     callback_data="m_list"),
         InlineKeyboardButton("🚀 Run download",   callback_data="m_run")],
        [InlineKeyboardButton("⏹️ Stop",           callback_data="m_stop"),
         InlineKeyboardButton("📜 History",        callback_data="m_history")],
        [InlineKeyboardButton("🍪 Set cookies",    callback_data="m_cookies"),
         InlineKeyboardButton("📊 Status",         callback_data="m_status")],
        [InlineKeyboardButton("📡 Set channel",    callback_data="m_channel")],
    ])

def platform_kb(prefix: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(p.capitalize(), callback_data=f"{prefix}_{p}")]
            for p in PLATFORMS]
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="m_back")])
    return InlineKeyboardMarkup(rows)

def media_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📷 Photos only",       callback_data="dl_1")],
        [InlineKeyboardButton("🎬 Videos only",       callback_data="dl_2")],
        [InlineKeyboardButton("📦 Both (separately)", callback_data="dl_3")],
        [InlineKeyboardButton("🔙 Back",              callback_data="m_back")],
    ])

def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="m_back")]])

# ── MENU TEXT ─────────────────────────────────────────────────────────────────
def menu_text(uid: int, username: str, name: str) -> str:
    ch = get_channel(uid)
    ch_line = f"  📡 Output channel : *{ch}*\n" if ch else ""
    return (
        f"👤 *@{username}* — {name}\n"
        f"🪪 ID: `{uid}`  |  🆓 Free account\n"
        f"📨 Downloaded files: *{total_sent(uid)}*\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"  🗂 Sources queued : *{source_count(uid)}*\n"
        f"  🍪 Cookies ready  : *{cookie_status(uid)}*\n"
        f"  📨 Total sent     : *{total_sent(uid)}*\n"
        f"{ch_line}"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📥 *Media Downloader Bot*\n"
        "Bulk-download from social profiles to Telegram.\n\n"
        "*Features:*\n"
        "🔖 Albums · ⚡️ Real-time · ⏹ Stop/Resume\n"
        "🎬 Instagram · TikTok · Facebook · X\n"
        "📡 Post to channel · 🍪 Cookie auth · 📜 History\n\n"
        "👨‍💻 Dev: @copyrightpost"
    )

# ── /start ────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid  = user.id
    user_dir(uid)
    text = menu_text(uid, user.username or "user", user.full_name)
    await update.effective_message.reply_text(
        text, reply_markup=main_menu_kb(), parse_mode="Markdown")
    return ST_MAIN

# ── SHOW MENU (edit or new) ───────────────────────────────────────────────────
async def show_menu(msg, uid: int, username: str, name: str, edit=False):
    text = menu_text(uid, username, name)
    if edit:
        await msg.edit_text(text, reply_markup=main_menu_kb(), parse_mode="Markdown")
    else:
        await msg.reply_text(text, reply_markup=main_menu_kb(), parse_mode="Markdown")

# ── CALLBACK ROUTER ───────────────────────────────────────────────────────────
async def cb_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q       = update.callback_query
    data    = q.data
    user    = q.from_user
    uid     = user.id
    uname   = user.username or "user"
    name    = user.full_name
    await q.answer()

    if data == "m_back":
        await show_menu(q.message, uid, uname, name, edit=True)
        return ST_MAIN

    if data == "m_add":
        await q.message.edit_text(
            "➕ *Add source*\nChoose platform:",
            reply_markup=platform_kb("add"), parse_mode="Markdown")
        return ST_ADD_PLAT

    if data == "m_remove":
        await q.message.edit_text(
            "🗑️ *Remove source*\nChoose platform:",
            reply_markup=platform_kb("rem"), parse_mode="Markdown")
        return ST_REM_PLAT

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

    if data == "m_run":
        if not any(load_profiles(uid, p) for p in PLATFORMS):
            await q.message.edit_text(
                "⚠️ No sources yet. Add one first.", reply_markup=back_kb())
            return ST_MAIN
        await q.message.edit_text(
            "▶️ *What to download?*", reply_markup=media_kb(), parse_mode="Markdown")
        return ST_MEDIA_CHOICE

    if data == "m_stop":
        ev = STOP_EVENTS.get(uid)
        if ev and not ev.is_set():
            ev.set()
            await q.message.edit_text("⏹️ *Stop signal sent.*",
                                       reply_markup=back_kb(), parse_mode="Markdown")
        else:
            await q.message.edit_text("Nothing is running.", reply_markup=back_kb())
        return ST_MAIN

    if data == "m_history":
        h = load_history(uid)
        if not h:
            text = "No history yet."
        else:
            rows = [f"• {e['date']}  {e['platform']} `{e['user']}`  {e['sent']} files"
                    for e in h[:10]]
            text = "*Last 10 downloads:*\n" + "\n".join(rows)
        await q.message.edit_text(text, reply_markup=back_kb(), parse_mode="Markdown")
        return ST_MAIN

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

    if data == "m_status":
        lines = ["*Platform status:*"]
        for p in PLATFORMS:
            n  = len(load_profiles(uid, p))
            ck = "✅" if (cookie_dir(uid) / PLATFORMS[p][1]).exists() else "❌"
            lines.append(f"  {p.capitalize():<12} profiles: {n}  cookies: {ck}")
        ch = get_channel(uid)
        lines.append(f"\n📡 Output channel: *{ch or 'not set'}*")
        await q.message.edit_text("\n".join(lines), reply_markup=back_kb(), parse_mode="Markdown")
        return ST_MAIN

    if data == "m_channel":
        ch = get_channel(uid)
        current = f"Current: *{ch}*" if ch else "Not set yet."
        text = (
            "📡 *Set output channel*\n\n"
            f"{current}\n\n"
            "Send the channel username or ID:\n"
            "`@yourchannel` or `-100xxxxxxxxxx`\n\n"
            "The bot must be an *admin* in that channel.\n"
            "Send `clear` to remove the channel."
        )
        ctx.user_data["awaiting"] = "channel"
        await q.message.edit_text(text, reply_markup=back_kb(), parse_mode="Markdown")
        return ST_SET_CHANNEL

    # Platform chosen for Add
    if data.startswith("add_"):
        platform = data.split("_", 1)[1]
        ctx.user_data["add_platform"] = platform
        await q.message.edit_text(
            f"➕ *Add {platform.capitalize()}*\n\nSend profile URL:\n`{PLATFORM_URLS[platform]}username/`",
            reply_markup=back_kb(), parse_mode="Markdown")
        return ST_ADD_URL

    # Platform chosen for Remove
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

    # Delete a specific URL
    if data.startswith("del_"):
        _, rest       = data.split("_", 1)
        platform, url = rest.split("|||", 1)
        urls = load_profiles(uid, platform)
        if url in urls:
            urls.remove(url)
            save_profiles(uid, platform, urls)
        await show_menu(q.message, uid, uname, name, edit=True)
        return ST_MAIN

    # Download type chosen
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
    uname   = user.username or "user"
    name    = user.full_name
    text    = update.message.text.strip()
    waiting = ctx.user_data.get("awaiting")

    # Channel input
    if waiting == "channel":
        ctx.user_data.pop("awaiting", None)
        if text.lower() == "clear":
            set_channel(uid, None)
            await update.message.reply_text("📡 Channel removed.")
        else:
            set_channel(uid, text)
            await update.message.reply_text(
                f"📡 Output channel set to *{text}*\n"
                "Make sure the bot is admin there.",
                parse_mode="Markdown")
        await show_menu(update.message, uid, uname, name)
        return ST_MAIN

    # Add profile URL
    platform = ctx.user_data.get("add_platform")
    if not platform:
        return ST_MAIN
    urls = load_profiles(uid, platform)
    if text in urls:
        await update.message.reply_text("Already in list.")
    else:
        urls.append(text)
        save_profiles(uid, platform, urls)
        await update.message.reply_text(
            f"✅ Added to *{platform}*.", parse_mode="Markdown")
    ctx.user_data.pop("add_platform", None)
    await show_menu(update.message, uid, uname, name)
    return ST_MAIN

# ── COOKIE UPLOAD ─────────────────────────────────────────────────────────────
async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user  = update.effective_user
    uid   = user.id
    doc   = update.message.document
    name  = doc.file_name
    known = {PLATFORMS[p][1] for p in PLATFORMS}
    if name not in known:
        await update.message.reply_text(
            f"⚠ Unknown file `{name}`.\nExpected:\n" + "\n".join(f"`{k}`" for k in sorted(known)),
            parse_mode="Markdown")
        return
    tg_file = await doc.get_file()
    dest    = cookie_dir(uid) / name
    await tg_file.download_to_drive(str(dest))
    await update.message.reply_text(f"✅ Cookie saved: `{name}`", parse_mode="Markdown")

# ── ALBUM SENDER ──────────────────────────────────────────────────────────────
async def send_album(target, batch: list[Path], media: str):
    """Send up to 10 files as album. target = message or chat_id."""
    if not batch:
        return
    try:
        if media == "photos":
            group = [InputMediaPhoto(open(f, "rb")) for f in batch]
        else:
            group = [InputMediaVideo(open(f, "rb")) for f in batch]
        if hasattr(target, "reply_media_group"):
            await target.reply_media_group(group)
        else:
            # target is a bot + chat_id tuple
            bot, chat_id = target
            await bot.send_media_group(chat_id=chat_id, media=group)
    except Exception:
        for f in batch:
            try:
                if hasattr(target, "reply_document"):
                    await target.reply_document(document=open(f, "rb"))
                else:
                    bot, chat_id = target
                    await bot.send_document(chat_id=chat_id, document=open(f, "rb"))
            except Exception:
                pass

# ── REALTIME DOWNLOAD ─────────────────────────────────────────────────────────
async def realtime_download(send_target, out_dir: Path, cookie: Path,
                             sleep: int, url: str, media: str,
                             stop: asyncio.Event) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    exts = PHOTO_EXT if media == "photos" else VIDEO_EXT
    filt = ("extension in ('jpg','jpeg','png','gif','webp','bmp')"
            if media == "photos" else
            "extension in ('mp4','webm','mkv','mov','avi','m4v')")

    cmd = ["gallery-dl", "-D", str(out_dir),
           "--filter", filt,
           "--download-archive", str(out_dir / "archive.txt"),
           "--sleep-request", str(sleep)]
    if cookie.exists():
        cmd += ["--cookies", str(cookie)]
    cmd.append(url)

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)

    seen: set[Path] = set()
    buf:  list[Path] = []
    sent = 0

    async def flush():
        nonlocal sent
        if buf:
            await send_album(send_target, list(buf), media)
            sent += len(buf)
            buf.clear()

    while proc.returncode is None:
        if stop.is_set():
            proc.kill(); break
        await asyncio.sleep(2)
        if out_dir.exists():
            for f in sorted(out_dir.iterdir()):
                if f in seen or not f.is_file() or f.suffix.lower() not in exts:
                    continue
                seen.add(f); buf.append(f)
                if len(buf) == 10:
                    await flush()
        try:
            await asyncio.wait_for(proc.wait(), timeout=0.1)
        except asyncio.TimeoutError:
            pass

    if not stop.is_set() and out_dir.exists():
        for f in sorted(out_dir.iterdir()):
            if f in seen or not f.is_file() or f.suffix.lower() not in exts:
                continue
            seen.add(f); buf.append(f)
            if len(buf) == 10:
                await flush()
    await flush()
    return sent

# ── DOWNLOAD ORCHESTRATOR ─────────────────────────────────────────────────────
async def do_download(msg, choice: str, uid: int,
                      uname: str, name: str, bot):
    stop        = STOP_EVENTS.get(uid, asyncio.Event())
    label       = {"1": "Photos only", "2": "Videos only", "3": "Both"}.get(choice, "Both")
    media_types = []
    if choice in ("1", "3"): media_types.append("photos")
    if choice in ("2", "3"): media_types.append("videos")

    # Determine send target: channel or current chat
    channel = get_channel(uid)
    send_target = (bot, channel) if channel else msg

    status = await msg.reply_text(
        f"⏳ *{label}* — starting…"
        + (f"\n📡 Sending to {channel}" if channel else ""),
        parse_mode="Markdown")

    total = 0
    start = datetime.now()

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
            for media in media_types:
                if stop.is_set(): break
                out_dir = (DATA_ROOT / str(uid) / "downloads"
                           / platform.capitalize() / user_handle / media.capitalize())
                n = await realtime_download(
                    send_target, out_dir,
                    cookie_dir(uid) / cfile,
                    sleep, url, media, stop)
                total += n
                if n > 0:
                    save_history(uid, {
                        "date":     datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "platform": platform,
                        "user":     user_handle,
                        "media":    media,
                        "sent":     n,
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
            ST_MAIN:         [CallbackQueryHandler(cb_router)],
            ST_ADD_PLAT:     [CallbackQueryHandler(cb_router)],
            ST_ADD_URL:      [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
                              CallbackQueryHandler(cb_router)],
            ST_REM_PLAT:     [CallbackQueryHandler(cb_router)],
            ST_REM_URL:      [CallbackQueryHandler(cb_router)],
            ST_MEDIA_CHOICE: [CallbackQueryHandler(cb_router)],
            ST_SET_CHANNEL:  [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
                              CallbackQueryHandler(cb_router)],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
        per_message=False,
        per_user=True,
        per_chat=False,       # user data follows the user across chats/groups
    )

    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    print("Bot running…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
