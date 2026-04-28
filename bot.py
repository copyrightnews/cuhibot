#!/usr/bin/env python3
"""
Cuhi Bot — Media Downloader  (fixed edition)
Fixes:
  • build_cmd: complete filter strings for videos / documents / stories / highlights
  • Stories URL:    https://www.instagram.com/stories/<user>/
  • Highlights URL: https://www.instagram.com/<user>/highlights/
  • Media groups: single-file → send_photo/send_video; 2-10 → send_media_group batches
  • Videos-only & docs mode: correct InputMedia types + extension filtering
  • Per-profile archive files (no cross-profile skipping)
  • File-handle leaks: all open() wrapped in `with`
  • Robust subprocess error capture
"""

import asyncio
import json
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
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
ALL_EXT   = PHOTO_EXT | VIDEO_EXT

# Telegram media_group limits
TG_GROUP_MIN = 2
TG_GROUP_MAX = 10

STOP_EVENTS: dict[int, asyncio.Event] = {}

# Conversation states
(ST_MAIN, ST_ADD_PLAT, ST_ADD_URL, ST_REM_PLAT, ST_REM_URL,
 ST_MEDIA_CHOICE, ST_SET_CHANNEL, ST_STORY_PLAT, ST_HIGHLIGHT_PLAT,
 ST_STORY_URL, ST_HIGHLIGHT_URL) = range(11)

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
    if not f.exists():
        return []
    with open(f) as fh:
        return [l.strip() for l in fh if l.strip()]

def save_profiles(uid: int, p: str, urls: list[str]) -> None:
    with open(profile_file(uid, p), "w") as fh:
        fh.write("\n".join(urls) + "\n")

def load_history(uid: int) -> list:
    f = history_file(uid)
    if not f.exists():
        return []
    with open(f) as fh:
        return json.load(fh)

def save_history(uid: int, entry: dict) -> None:
    h = load_history(uid)
    h.insert(0, entry)
    with open(history_file(uid), "w") as fh:
        json.dump(h[:50], fh, indent=2)

def load_settings(uid: int) -> dict:
    f = settings_file(uid)
    if not f.exists():
        return {}
    with open(f) as fh:
        return json.load(fh)

def save_settings(uid: int, data: dict) -> None:
    with open(settings_file(uid), "w") as fh:
        json.dump(data, fh, indent=2)

def get_channel(uid: int):
    return load_settings(uid).get("channel")

def set_channel(uid: int, channel) -> None:
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

# ── MENU TEXT ─────────────────────────────────────────────────────────────────
def menu_text(uid: int, username: str, name: str) -> str:
    ch      = get_channel(uid)
    ch_line = f"\n📡 Output: *{ch}*" if ch else ""
    return (
        f"@{username}, {name}\n"
        f"👤 ID: `{uid}`\n"
        f"🧾 Free account\n"
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
        "👤\u200d💻 Developer: @copyrightpost"
    )

# ── KEYBOARDS ─────────────────────────────────────────────────────────────────
def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add source",       callback_data="m_add"),
         InlineKeyboardButton("🚫 Remove source",   callback_data="m_remove")],
        [InlineKeyboardButton("🌐 My sources",       callback_data="m_list"),
         InlineKeyboardButton("✅ Run download",     callback_data="m_run")],
        [InlineKeyboardButton("📖 Stories",          callback_data="m_stories"),
         InlineKeyboardButton("✨ Highlights",       callback_data="m_highlights")],
        [InlineKeyboardButton("🚫 Stop download",    callback_data="m_stop"),
         InlineKeyboardButton("📜 History",          callback_data="m_history")],
        [InlineKeyboardButton("🍪 Set cookies",      callback_data="m_cookies"),
         InlineKeyboardButton("📊 Status",           callback_data="m_status")],
        [InlineKeyboardButton("📡 Set channel",      callback_data="m_channel")],
    ])

def platform_kb(prefix: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(p.capitalize(), callback_data=f"{prefix}_{p}")]
            for p in PLATFORMS]
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="m_back")])
    return InlineKeyboardMarkup(rows)

def media_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼️ Photos only",       callback_data="dl_1")],
        [InlineKeyboardButton("🎬 Videos only",       callback_data="dl_2")],
        [InlineKeyboardButton("🔖 Both (separately)", callback_data="dl_3")],
        [InlineKeyboardButton("📁 Files (as docs)",   callback_data="dl_4")],
        [InlineKeyboardButton("🔙 Back",              callback_data="m_back")],
    ])

def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="m_back")]])

# ── SHOW MENU ─────────────────────────────────────────────────────────────────
async def show_menu(msg, uid: int, username: str, name: str, edit: bool = False):
    text = menu_text(uid, username, name)
    kb   = main_menu_kb()
    try:
        if edit:
            await msg.edit_text(text, reply_markup=kb, parse_mode="Markdown")
        else:
            await msg.reply_text(text, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        short = (
            f"@{username} — {name}\n"
            f"ID: `{uid}` | Free account\n"
            f"✅ Downloaded Media: *{total_sent(uid)}*\n\n"
            f"🌐 Sources: *{source_count(uid)}*\n"
            f"🍪 Cookies: *{cookie_status(uid)}*"
        )
        if edit:
            await msg.edit_text(short, reply_markup=kb, parse_mode="Markdown")
        else:
            await msg.reply_text(short, reply_markup=kb, parse_mode="Markdown")

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

# ── GALLERY-DL COMMAND BUILDER ────────────────────────────────────────────────
def build_cmd(out_dir: Path, cookie: Path | None, sleep: int, url: str, mode: str) -> list[str]:
    """
    Build gallery-dl command list.

    mode values:
      photos     — images only  (--filter by extension)
      videos     — videos only  (--filter by extension)
      both       — all media, no filter (caller separates)
      documents  — all media, no filter (caller sends as docs)
      stories    — Instagram stories (URL already adjusted by caller)
      highlights — Instagram highlights (URL already adjusted by caller)
    """
    cmd = [
        "gallery-dl",
        "--no-mtime",
        "-D", str(out_dir),
        "--download-archive", str(out_dir / "archive.txt"),
        "--sleep-request", str(sleep),
    ]

    # Add cookies if they exist
    if cookie and cookie.exists():
        cmd += ["--cookies", str(cookie)]

    # Extension filters — FIX: complete, correct filter strings
    if mode == "photos":
        cmd += ["--filter",
                "extension.lower() in ('jpg','jpeg','png','gif','webp','bmp')"]
    elif mode == "videos":
        cmd += ["--filter",
                "extension.lower() in ('mp4','webm','mkv','mov','avi','m4v')"]
    # both / documents / stories / highlights → no filter, download everything

    cmd.append(url)
    return cmd

# ── SEND HELPERS ──────────────────────────────────────────────────────────────
def _chunks(lst: list, n: int):
    """Yield successive n-sized chunks from lst."""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]

async def send_files(bot, chat_id: int | str, files: list[Path], mode: str,
                     caption: str = "") -> int:
    """
    Send a list of local files to chat_id.
    mode: 'photos' | 'videos' | 'both' | 'documents'
    Returns number of files sent.
    """
    if not files:
        return 0

    sent = 0

    if mode == "documents":
        # Send every file as a raw document, no media-group batching needed
        for batch in _chunks(files, TG_GROUP_MAX):
            if len(batch) == 1:
                with open(batch[0], "rb") as fh:
                    await bot.send_document(chat_id=chat_id, document=fh,
                                            caption=caption if sent == 0 else "")
                sent += 1
            else:
                media = []
                for i, fp in enumerate(batch):
                    fh = open(fp, "rb")          # closed after send below
                    media.append(InputMediaDocument(
                        media=fh,
                        caption=caption if (sent == 0 and i == 0) else ""))
                try:
                    await bot.send_media_group(chat_id=chat_id, media=media)
                    sent += len(batch)
                finally:
                    for item in media:
                        try:
                            item.media.close()
                        except Exception:
                            pass
        return sent

    # For photos / videos / both, separate by type then send
    photo_files = [f for f in files if f.suffix.lower() in PHOTO_EXT]
    video_files = [f for f in files if f.suffix.lower() in VIDEO_EXT]

    if mode == "photos":
        to_send_groups = [("photo", photo_files)]
    elif mode == "videos":
        to_send_groups = [("video", video_files)]
    else:  # both
        to_send_groups = [("photo", photo_files), ("video", video_files)]

    first_caption = caption
    for media_type, file_list in to_send_groups:
        if not file_list:
            continue
        for batch in _chunks(file_list, TG_GROUP_MAX):
            cap = first_caption
            first_caption = ""   # only first batch gets caption

            # ── SINGLE FILE ──────────────────────────────────────────────────
            if len(batch) == 1:
                fp = batch[0]
                with open(fp, "rb") as fh:
                    if media_type == "photo":
                        await bot.send_photo(chat_id=chat_id, photo=fh, caption=cap)
                    else:
                        await bot.send_video(chat_id=chat_id, video=fh, caption=cap)
                sent += 1

            # ── MEDIA GROUP (2–10) ───────────────────────────────────────────
            else:
                handles = [open(fp, "rb") for fp in batch]
                media = []
                for i, (fh, fp) in enumerate(zip(handles, batch)):
                    item_cap = cap if i == 0 else ""
                    if media_type == "photo":
                        media.append(InputMediaPhoto(media=fh, caption=item_cap))
                    else:
                        media.append(InputMediaVideo(media=fh, caption=item_cap))
                try:
                    await bot.send_media_group(chat_id=chat_id, media=media)
                    sent += len(batch)
                finally:
                    for fh in handles:
                        try:
                            fh.close()
                        except Exception:
                            pass

    return sent

# ── GALLERY-DL RUNNER ─────────────────────────────────────────────────────────
async def run_gallery_dl(cmd: list[str]) -> tuple[int, str]:
    """Run gallery-dl in a thread. Returns (returncode, stderr_output)."""
    loop = asyncio.get_event_loop()

    def _run():
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result.returncode, result.stderr

    return await loop.run_in_executor(None, _run)

# ── MAIN DOWNLOAD TASK ────────────────────────────────────────────────────────
async def do_download(msg, choice: str, uid: int, uname: str, name: str, bot):
    """
    choice:
      1 → photos only
      2 → videos only
      3 → both
      4 → documents
    """
    mode_map = {"1": "photos", "2": "videos", "3": "both", "4": "documents"}
    mode     = mode_map.get(choice, "both")
    stop_ev  = STOP_EVENTS.get(uid, asyncio.Event())

    target   = get_channel(uid) or msg.chat_id
    total    = 0
    errors   = []

    status_msg = await bot.send_message(
        chat_id=msg.chat_id,
        text=f"⏳ Starting download… (mode: {mode})")

    for platform in PLATFORMS:
        urls = load_profiles(uid, platform)
        if not urls:
            continue

        sleep      = PLATFORMS[platform][2]
        cookie_path = cookie_dir(uid) / PLATFORMS[platform][1]

        for profile_url in urls:
            if stop_ev.is_set():
                break

            # Per-profile temp directory so archive files don't collide
            with tempfile.TemporaryDirectory(prefix=f"cuhibot_{uid}_") as tmpdir:
                out_dir = Path(tmpdir)
                cmd     = build_cmd(out_dir, cookie_path, sleep, profile_url, mode)

                await bot.edit_message_text(
                    chat_id=msg.chat_id,
                    message_id=status_msg.message_id,
                    text=f"⬇️ Downloading *{platform}* `{profile_url}`…",
                    parse_mode="Markdown")

                rc, stderr = await run_gallery_dl(cmd)

                if rc not in (0, 1):   # gallery-dl: 0=ok, 1=some errors but continued
                    errors.append(f"{platform}: {profile_url} (rc={rc})")
                    if stderr:
                        errors.append(f"  ↳ {stderr[:200]}")
                    continue

                # Collect downloaded files sorted by modification time
                downloaded = sorted(
                    [f for f in out_dir.rglob("*")
                     if f.is_file()
                     and f.suffix.lower() in ALL_EXT
                     and f.name != "archive.txt"],
                    key=lambda f: f.stat().st_mtime
                )

                if not downloaded:
                    continue

                caption = f"📥 {platform.capitalize()} — {profile_url.rstrip('/').split('/')[-1]}"
                n = await send_files(bot, target, downloaded, mode, caption)
                total += n

        if stop_ev.is_set():
            break

    # History entry
    save_history(uid, {
        "date":     datetime.now().strftime("%Y-%m-%d %H:%M"),
        "platform": "all",
        "user":     "multiple",
        "sent":     total,
    })

    summary = f"✅ Done! Sent *{total}* file(s)."
    if errors:
        summary += "\n\n⚠️ Errors:\n" + "\n".join(errors[:5])

    await bot.edit_message_text(
        chat_id=msg.chat_id,
        message_id=status_msg.message_id,
        text=summary,
        parse_mode="Markdown")

    await show_menu(await bot.send_message(chat_id=msg.chat_id, text="↩️ Menu"),
                    uid, uname, name)

# ── SPECIAL DOWNLOAD (Stories / Highlights) ───────────────────────────────────
def _story_url(platform: str, profile_url: str) -> str:
    """
    Convert a profile URL to a stories URL for gallery-dl.
    Instagram:  https://www.instagram.com/stories/username/
    Others:     return as-is (not supported)
    """
    if platform != "instagram":
        return profile_url
    # Extract username from URL
    username = profile_url.rstrip("/").split("/")[-1]
    return f"https://www.instagram.com/stories/{username}/"

def _highlight_url(platform: str, profile_url: str) -> str:
    """
    Convert a profile URL to a highlights URL for gallery-dl.
    Instagram:  https://www.instagram.com/username/highlights/
    """
    if platform != "instagram":
        return profile_url
    username = profile_url.rstrip("/").split("/")[-1]
    return f"https://www.instagram.com/{username}/highlights/"

async def do_special_download(msg, profile_url: str, platform: str,
                               kind: str, uid: int, uname: str, name: str, bot):
    """kind: 'stories' or 'highlights'"""
    stop_ev = STOP_EVENTS.get(uid, asyncio.Event())
    target  = get_channel(uid) or msg.chat_id

    if kind == "stories":
        dl_url = _story_url(platform, profile_url)
    else:
        dl_url = _highlight_url(platform, profile_url)

    cookie_path = cookie_dir(uid) / PLATFORMS[platform][1]
    sleep       = PLATFORMS[platform][2]

    status_msg = await bot.send_message(
        chat_id=msg.chat_id,
        text=f"⏳ Fetching {kind} from `{dl_url}`…",
        parse_mode="Markdown")

    with tempfile.TemporaryDirectory(prefix=f"cuhibot_{uid}_") as tmpdir:
        out_dir = Path(tmpdir)
        # stories/highlights: download all media (photos + videos)
        cmd = build_cmd(out_dir, cookie_path, sleep, dl_url, kind)
        rc, stderr = await run_gallery_dl(cmd)

        if rc not in (0, 1):
            err_text = f"❌ gallery-dl error (rc={rc})"
            if stderr:
                err_text += f"\n`{stderr[:300]}`"
            await bot.edit_message_text(
                chat_id=msg.chat_id,
                message_id=status_msg.message_id,
                text=err_text,
                parse_mode="Markdown")
            return

        downloaded = sorted(
            [f for f in out_dir.rglob("*")
             if f.is_file()
             and f.suffix.lower() in ALL_EXT
             and f.name != "archive.txt"],
            key=lambda f: f.stat().st_mtime
        )

        if not downloaded:
            await bot.edit_message_text(
                chat_id=msg.chat_id,
                message_id=status_msg.message_id,
                text=f"ℹ️ No {kind} found (or already downloaded).")
            return

        caption = (f"{'📖' if kind == 'stories' else '✨'} "
                   f"{platform.capitalize()} {kind} — "
                   f"{profile_url.rstrip('/').split('/')[-1]}")

        n = await send_files(bot, target, downloaded, "both", caption)

        save_history(uid, {
            "date":     datetime.now().strftime("%Y-%m-%d %H:%M"),
            "platform": platform,
            "user":     profile_url.rstrip("/").split("/")[-1],
            "sent":     n,
        })

        await bot.edit_message_text(
            chat_id=msg.chat_id,
            message_id=status_msg.message_id,
            text=f"✅ Sent *{n}* {kind} file(s).",
            parse_mode="Markdown")

    await show_menu(await bot.send_message(chat_id=msg.chat_id, text="↩️ Menu"),
                    uid, uname, name)

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
            "❔ *What to download?*\n\n"
            "🖼️ *Photos only* — sends as images\n"
            "🎬 *Videos only* — sends as videos\n"
            "🔖 *Both* — photos first then videos\n"
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
            "✨ *Download Highlights*\nChoose platform:",
            reply_markup=platform_kb("highlight"), parse_mode="Markdown")
        return ST_HIGHLIGHT_PLAT

    # ── STOP ──
    if data == "m_stop":
        ev = STOP_EVENTS.get(uid)
        if ev and not ev.is_set():
            ev.set()
            await q.message.edit_text(
                "🚫 *Stop signal sent.*\nFinishing current album then stopping.",
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
        await q.message.edit_text(
            "\n".join(lines), reply_markup=back_kb(), parse_mode="Markdown")
        return ST_MAIN

    # ── SET CHANNEL ──
    if data == "m_channel":
        ch      = get_channel(uid)
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
        urls     = load_profiles(uid, platform)
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
        urls          = load_profiles(uid, platform)
        if url in urls:
            urls.remove(url)
            save_profiles(uid, platform, urls)
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
            f"✨ *Download {platform.capitalize()} Highlights*\n\n"
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

    # ── CHANNEL ──
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

    # ── STORY URL ──
    if ctx.user_data.get("story_platform"):
        platform = ctx.user_data.pop("story_platform")
        STOP_EVENTS[uid] = asyncio.Event()
        asyncio.create_task(
            do_special_download(update.message, text, platform,
                                "stories", uid, uname, name, ctx.bot))
        return ST_MAIN

    # ── HIGHLIGHT URL ──
    if ctx.user_data.get("highlight_platform"):
        platform = ctx.user_data.pop("highlight_platform")
        STOP_EVENTS[uid] = asyncio.Event()
        asyncio.create_task(
            do_special_download(update.message, text, platform,
                                "highlights", uid, uname, name, ctx.bot))
        return ST_MAIN

    # ── ADD PROFILE URL ──
    platform = ctx.user_data.get("add_platform")
    if platform:
        ctx.user_data.pop("add_platform", None)
        urls = load_profiles(uid, platform)
        if text in urls:
            await update.message.reply_text("Already in list.")
        else:
            urls.append(text)
            save_profiles(uid, platform, urls)
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
    dest    = cookie_dir(uid) / fname
    await tg_file.download_to_drive(str(dest))
    await update.message.reply_text(f"✅ Cookie saved: `{fname}`", parse_mode="Markdown")

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            ST_MAIN: [
                CallbackQueryHandler(cb_router),
                MessageHandler(filters.Document.ALL, handle_document),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
            ],
            ST_ADD_PLAT: [CallbackQueryHandler(cb_router)],
            ST_ADD_URL:  [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
                CallbackQueryHandler(cb_router),
            ],
            ST_REM_PLAT: [CallbackQueryHandler(cb_router)],
            ST_REM_URL:  [CallbackQueryHandler(cb_router)],
            ST_MEDIA_CHOICE: [CallbackQueryHandler(cb_router)],
            ST_SET_CHANNEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
                CallbackQueryHandler(cb_router),
            ],
            ST_STORY_PLAT:   [CallbackQueryHandler(cb_router)],
            ST_STORY_URL:    [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
                CallbackQueryHandler(cb_router),
            ],
            ST_HIGHLIGHT_PLAT: [CallbackQueryHandler(cb_router)],
            ST_HIGHLIGHT_URL:  [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
                CallbackQueryHandler(cb_router),
            ],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
        per_user=True,
        per_chat=True,
    )

    app.add_handler(conv)
    print("🤖 Cuhi Bot started…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
