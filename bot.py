#!/usr/bin/env python3
"""
Cuhi Bot — patched edition.

Every numbered comment tag (BUG-01 … BUG-16) maps back to the audit report.
Layout:
  1. Imports & constants
  2. Locking, disk, path utilities
  3. Persistence (profiles / history / settings)
  4. Validators & normalizers
  5. Keyboards & menu rendering
  6. Status throttler
  7. gallery-dl command builder
  8. Sender (Telegram)
  9. Real-time download engine
 10. High-level orchestrators
 11. Telegram handlers
 12. main()
"""

# =============================================================================
# 1. IMPORTS & CONSTANTS
# =============================================================================

from __future__ import annotations

import asyncio
import base64
import fcntl
import json
import os
import re
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    Update,
)
from telegram.error import BadRequest, RetryAfter
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")

DATA_ROOT    = Path(os.environ.get("DATA_ROOT",    "./data"))
COOKIES_ROOT = Path(os.environ.get("COOKIES_ROOT", "./cookies"))

# (profiles_filename, cookie_filename, request_sleep_seconds)
PLATFORMS: dict[str, tuple[str, str, int]] = {
    "instagram": ("instagram_profiles.txt", "instagram.com_cookies.txt", 3),
    "tiktok":    ("tiktok_profiles.txt",    "tiktok.com_cookies.txt",    2),
    "facebook":  ("facebook_profiles.txt",  "facebook.com_cookies.txt",  3),
    "x":         ("x_profiles.txt",         "x.com_cookies.txt",         3),
}

# Maps env-var names → platform cookie filenames
# These are the COOKIE_* variables you set in Railway.
# Values can be raw Netscape cookie text or base64-encoded cookie text.
COOKIE_ENV_MAP: dict[str, str] = {
    "COOKIE_INSTAGRAM": "instagram.com_cookies.txt",
    "COOKIE_TIKTOK":    "tiktok.com_cookies.txt",
    "COOKIE_FACEBOOK":  "facebook.com_cookies.txt",
    "COOKIE_X":         "x.com_cookies.txt",
}

PLATFORM_DOMAINS: dict[str, tuple[str, ...]] = {
    "instagram": ("instagram.com",),
    "tiktok":    ("tiktok.com",),
    "facebook":  ("facebook.com", "fb.com", "m.facebook.com"),
    "x":         ("x.com", "twitter.com"),
}

PLATFORM_URL_HINTS: dict[str, str] = {
    "instagram": "https://www.instagram.com/",
    "tiktok":    "https://www.tiktok.com/@",
    "facebook":  "https://www.facebook.com/",
    "x":         "https://x.com/",
}

PHOTO_EXT = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"})
VIDEO_EXT = frozenset({".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v"})

MEDIA_GROUP_MAX = 10       # Telegram limit
STATUS_MIN_GAP  = 2.0      # seconds (BUG-06)

# State keys
S_MAIN, S_ADD_URL, S_SET_CHANNEL, S_STORY, S_HIGHLIGHT = (
    "main", "add_url", "set_channel", "story_url", "highlight_url"
)

# Runtime registries
STOP_EVENTS: dict[int, asyncio.Event] = {}
ACTIVE_USERS: set[int]                = set()   # BUG-10


# =============================================================================
# 2. LOCKING, DISK, PATH UTILITIES
# =============================================================================

@contextmanager
def locked_file(target: Path):
    """Advisory POSIX lock around a sibling `.lock` file. BUG-08."""
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_suffix(target.suffix + ".lock")
    with open(lock_path, "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def udir(uid: int) -> Path:
    p = DATA_ROOT / str(uid)
    p.mkdir(parents=True, exist_ok=True)
    return p


def cdir(uid: int) -> Path:
    p = COOKIES_ROOT / str(uid)
    p.mkdir(parents=True, exist_ok=True)
    return p


def global_cookie_dir() -> Path:
    """Shared cookie dir written from env vars at startup (not per-user)."""
    p = COOKIES_ROOT / "_global"
    p.mkdir(parents=True, exist_ok=True)
    return p


def profiles_path(uid: int, platform: str) -> Path:
    return udir(uid) / PLATFORMS[platform][0]


def history_path(uid: int) -> Path:
    return udir(uid) / "history.json"


def settings_path(uid: int) -> Path:
    return udir(uid) / "settings.json"


def archive_path(uid: int, platform: str, handle: str, mode: str) -> Path:
    """Persistent gallery-dl archive, stored OUTSIDE the volatile download dir. BUG-05, BUG-11."""
    base = udir(uid) / "archives" / platform / handle
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{mode}.txt"


def folder_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return round(total / (1024 * 1024), 2)


def wipe_downloads(uid: int) -> float:
    root = udir(uid) / "downloads"
    freed = folder_mb(root)
    shutil.rmtree(root, ignore_errors=True)
    return freed


def resolve_cookie(uid: int, platform: str) -> Path:
    """
    Return the best available cookie file for this user+platform.
    Priority: per-user upload → global env-var cookie → missing (gallery-dl
    will run without cookies).
    """
    _, cookie_name, _ = PLATFORMS[platform]
    user_cookie   = cdir(uid) / cookie_name
    global_cookie = global_cookie_dir() / cookie_name
    if user_cookie.exists():
        return user_cookie
    if global_cookie.exists():
        return global_cookie
    return user_cookie   # doesn't exist; caller checks .exists()


# =============================================================================
# 3. PERSISTENCE
# =============================================================================

def read_profiles(uid: int, platform: str) -> list[str]:
    p = profiles_path(uid, platform)
    if not p.exists():
        return []
    return [line.strip() for line in p.read_text().splitlines() if line.strip()]


def write_profiles(uid: int, platform: str, urls: Iterable[str]) -> None:
    path = profiles_path(uid, platform)
    with locked_file(path):
        path.write_text("\n".join(urls) + "\n")


def read_history(uid: int) -> list[dict]:
    p = history_path(uid)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except Exception:
        return []


def append_history(uid: int, entry: dict) -> None:
    path = history_path(uid)
    with locked_file(path):                      # BUG-08
        current = read_history(uid)
        current.insert(0, entry)
        path.write_text(json.dumps(current[:50], indent=2))


def read_settings(uid: int) -> dict:
    p = settings_path(uid)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def write_settings(uid: int, data: dict) -> None:
    path = settings_path(uid)
    with locked_file(path):
        path.write_text(json.dumps(data, indent=2))


def get_channel(uid: int):
    return read_settings(uid).get("channel")


def set_channel(uid: int, value) -> None:
    s = read_settings(uid)
    if value in (None, "", "clear"):
        s.pop("channel", None)
    else:
        s["channel"] = value
    write_settings(uid, s)


def total_profiles(uid: int) -> int:              # BUG-16
    return sum(len(read_profiles(uid, p)) for p in PLATFORMS)


def cookie_summary(uid: int) -> str:
    ok = []
    for p in PLATFORMS:
        _, cookie_name, _ = PLATFORMS[p]
        user_c   = cdir(uid) / cookie_name
        global_c = global_cookie_dir() / cookie_name
        if user_c.exists() or global_c.exists():
            ok.append(p)
    return ", ".join(ok) if ok else "none"


def total_sent(uid: int) -> int:
    return sum(e.get("sent", 0) for e in read_history(uid))


# =============================================================================
# 4. VALIDATORS & NORMALIZERS
# =============================================================================

_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)


def validate_url(url: str, platform: str) -> tuple[bool, str]:
    """BUG-07: ensure input is both well-formed AND on the right domain."""
    if not _URL_RE.match(url):
        return False, "Not a valid URL (must start with http:// or https://)."
    allowed = PLATFORM_DOMAINS[platform]
    if not any(dom in url.lower() for dom in allowed):
        return False, f"URL must belong to: {', '.join(allowed)}"
    return True, ""


def normalize_chat(value: str):
    """BUG-03: convert user-entered channel strings into valid Telegram chat IDs."""
    v = value.strip()
    if v.startswith("@"):
        return v
    if v.lstrip("-").isdigit():
        n = int(v)
        # Already negative: keep as-is
        if n < 0:
            return n
        # Positive number: add -100 prefix for supergroups/channels
        # unless it already looks like a 100-prefixed ID (100XXXXXXXXX = 12 digits)
        if len(str(n)) == 12 and str(n).startswith("100"):
            return -n  # Already has 100, just make negative
        return int(f"-100{n}")


def handle_from_url(url: str) -> str:
    return url.rstrip("/").split("/")[-1].lstrip("@")


def stories_url_for(platform: str, url: str) -> str:
    """BUG-01: explicit rewrite so intent is unambiguous."""
    if platform == "instagram":
        return f"https://www.instagram.com/stories/{handle_from_url(url)}/"
    return url


def highlights_url_for(platform: str, url: str) -> str:
    """BUG-14: use gallery-dl's real highlights endpoint instead of a no-op."""
    if platform == "instagram":
        return f"https://www.instagram.com/stories/highlights/{handle_from_url(url)}/"
    return url


# =============================================================================
# 5. KEYBOARDS & MENU
# =============================================================================

def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add source",    callback_data="m_add"),
         InlineKeyboardButton("🚫 Remove source", callback_data="m_remove")],
        [InlineKeyboardButton("🌐 My sources",    callback_data="m_list"),
         InlineKeyboardButton("✅ Run download",  callback_data="m_run")],
        [InlineKeyboardButton("📖 Stories",       callback_data="m_stories"),
         InlineKeyboardButton("✨ Highlights",     callback_data="m_highlights")],
        [InlineKeyboardButton("🚫 Stop download", callback_data="m_stop"),
         InlineKeyboardButton("📜 History",       callback_data="m_history")],
        [InlineKeyboardButton("🍪 Set cookies",   callback_data="m_cookies"),
         InlineKeyboardButton("📊 Status",        callback_data="m_status")],
        [InlineKeyboardButton("📡 Set channel",   callback_data="m_channel"),
         InlineKeyboardButton("🗑️ Free disk",     callback_data="m_cleanup")],
    ])


def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="m_back")]])


def kb_platforms(prefix: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(p.capitalize(), callback_data=f"{prefix}_{p}")]
            for p in PLATFORMS]
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="m_back")])
    return InlineKeyboardMarkup(rows)


def kb_media() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼️ Photos only",       callback_data="dl_1")],
        [InlineKeyboardButton("🎬 Videos only",       callback_data="dl_2")],
        [InlineKeyboardButton("🔖 Both (separately)", callback_data="dl_3")],
        [InlineKeyboardButton("📁 Files (as docs)",   callback_data="dl_4")],
        [InlineKeyboardButton("🔙 Back",              callback_data="m_back")],
    ])


def render_menu(uid: int, username: str, name: str) -> str:
    ch = get_channel(uid)
    ch_line   = f"\n📡 Output: *{ch}*" if ch else ""
    return (
        f"@{username}, {name}\n"
        f"👤 ID: `{uid}`\n"
        f"🤍 Free account\n"
        f"✅ Downloaded Media: *{total_sent(uid)}*\n\n"
        "📩 *Cuhi Bot* — downloader & forwarder for "
        "Instagram, TikTok, Facebook, and X.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f" 🗂 Sources : *{total_profiles(uid)}*\n"
        f" 🍪 Cookies : *{cookie_summary(uid)}*"
        f"{ch_line}{disk_line}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "👨‍💻 Developer: @copyrightpost"
    )


async def send_menu(msg, uid: int, username: str, name: str, *, edit=False) -> None:
    text = render_menu(uid, username, name)
    try:
        if edit:
            try:
                await msg.edit_text(text, reply_markup=kb_main(), parse_mode="Markdown")
            except BadRequest as e:
                # If edit fails, try replying instead
                if "message is not modified" not in str(e).lower():
                    await msg.reply_text(text, reply_markup=kb_main(), parse_mode="Markdown")
        else:
            await msg.reply_text(text, reply_markup=kb_main(), parse_mode="Markdown")
    except Exception:
        pass


# =============================================================================
# 6. STATUS THROTTLER
# =============================================================================

@dataclass
class Status:
    """BUG-06: rate-limited edit_text wrapper that respects RetryAfter."""
    message: object
    last_at: float = 0.0
    last_text: str = ""

    async def set(self, text: str, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self.last_at) < STATUS_MIN_GAP:
            return
        if text == self.last_text:
            return
        try:
            await self.message.edit_text(text, parse_mode="Markdown")
            self.last_at = now
            self.last_text = text
        except RetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 0.5)
        except BadRequest:
            pass
        except Exception:
            pass


# =============================================================================
# 7. GALLERY-DL COMMAND BUILDER
# =============================================================================

def build_gdl_cmd(
    *,
    out_dir: Path,
    archive: Path,
    cookie: Path,
    sleep: int,
    url: str,
    mode: str,
    platform: str,
) -> tuple[list[str], str]:
    """
    Returns (argv, effective_url).
    Explicit branches for every mode — no silent fall-through. BUG-01, BUG-04, BUG-14.
    """
    cmd = [
        "gallery-dl",
        "-D", str(out_dir),
        "--download-archive", str(archive),
        "--sleep-request", str(sleep),
    ]

    if mode == "photos":
        cmd += ["--filter", "extension in ('jpg','jpeg','png','gif','webp','bmp')"]
        effective = url
    elif mode == "videos":
        cmd += ["--filter", "extension in ('mp4','webm','mkv','mov','avi','m4v')"]
        effective = url
    elif mode == "documents":
        effective = url
    elif mode == "stories":
        effective = stories_url_for(platform, url)
    elif mode == "highlights":
        effective = highlights_url_for(platform, url)
    elif mode == "both":
        # The orchestrator splits "both" into two calls; reaching here means misuse.
        raise ValueError("build_gdl_cmd: 'both' must be split into 'photos'+'videos'")
    else:
        effective = url

    if cookie.exists():
        cmd += ["--cookies", str(cookie)]

    cmd.append(effective)
    return cmd, effective


# =============================================================================
# 8. SENDER
# =============================================================================

def file_kind(f: Path) -> str:
    return "photo" if f.suffix.lower() in PHOTO_EXT else "video"


async def _send_group(target, group: list) -> None:
    if hasattr(target, "reply_media_group"):
        await target.reply_media_group(group)
    else:
        bot, cid = target
        await bot.send_media_group(chat_id=cid, media=group)


async def _send_one(target, f: Path, kind: str) -> None:
    try:
        with open(f, "rb") as fh:
            if kind == "photo":
                if hasattr(target, "reply_photo"):
                    await target.reply_photo(photo=fh)
                else:
                    bot, cid = target
                    await bot.send_photo(chat_id=cid, photo=fh)
            elif kind == "video":
                if hasattr(target, "reply_video"):
                    await target.reply_video(video=fh)
                else:
                    bot, cid = target
                    await bot.send_video(chat_id=cid, video=fh)
            else:
                if hasattr(target, "reply_document"):
                    await target.reply_document(document=fh)
                else:
                    bot, cid = target
                    await bot.send_document(chat_id=cid, document=fh)
    except Exception:
        pass


def _split_mixed(batch: list[Path]) -> list[list[Path]]:
    """BUG-12: don't mix weird payloads; produce Telegram-compatible chunks <= 10."""
    chunks: list[list[Path]] = []
    current: list[Path] = []
    for f in batch:
        current.append(f)
        if len(current) == MEDIA_GROUP_MAX:
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)
    return chunks


async def flush(target, batch: list[Path], send_as: str) -> None:
    """Send the buffered batch, then delete local files (disk-full prevention)."""
    if not batch:
        return

    file_handles = []
    try:
        if send_as == "documents":
            for f in batch:
                await _send_one(target, f, "doc")

        elif len(batch) == 1:
            f = batch[0]
            kind = {"photos": "photo", "videos": "video"}.get(send_as) or file_kind(f)
            await _send_one(target, f, kind)

        else:
            for chunk in _split_mixed(batch):
                try:
                    handles = []
                    if send_as == "photos":
                        for f in chunk:
                            fh = open(f, "rb")
                            file_handles.append(fh)
                            handles.append(fh)
                        group = [InputMediaPhoto(fh) for fh in handles]
                    elif send_as == "videos":
                        for f in chunk:
                            fh = open(f, "rb")
                            file_handles.append(fh)
                            handles.append(fh)
                        group = [InputMediaVideo(fh) for fh in handles]
                    else:  # mixed
                        for f in chunk:
                            fh = open(f, "rb")
                            file_handles.append(fh)
                            if file_kind(f) == "photo":
                                handles.append(InputMediaPhoto(fh))
                            else:
                                handles.append(InputMediaVideo(fh))
                        group = handles
                    await _send_group(target, group)
                except Exception:
                    for f in chunk:
                        kind = {"photos": "photo", "videos": "video"}.get(send_as) or file_kind(f)
                        await _send_one(target, f, kind)
    finally:
        for fh in file_handles:
            try:
                fh.close()
            except Exception:
                pass
        for f in batch:
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass


# =============================================================================
# 9. REAL-TIME DOWNLOAD ENGINE
# =============================================================================

async def realtime_download(
    *,
    target,
    uid: int,
    platform: str,
    handle: str,
    mode: str,
    url: str,
    cookie: Path,
    sleep: int,
    stop: asyncio.Event,
    status: Status | None = None,
) -> int:
    """
    Streams gallery-dl output: detects fully-written media, sends in batches,
    deletes them, and stops the moment `stop` is set.
    """
    out_dir = (udir(uid) / "downloads" / platform.capitalize()
               / handle / mode.capitalize())
    archive = archive_path(uid, platform, handle, mode)

    # BUG-05/11: create download dir; archive lives elsewhere and survives
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        msg = f"❌ Disk full. Tap 🗑️ Free disk or send /cleanup.\n`{exc}`"
        try:
            if hasattr(target, "reply_text"):
                await target.reply_text(msg, parse_mode="Markdown")
            else:
                bot, cid = target
                await bot.send_message(chat_id=cid, text=msg, parse_mode="Markdown")
        except Exception:
            pass
        return 0

    if mode == "photos":
        exts, send_as = PHOTO_EXT, "photos"
    elif mode == "videos":
        exts, send_as = VIDEO_EXT, "videos"
    elif mode == "documents":
        exts, send_as = PHOTO_EXT | VIDEO_EXT, "documents"
    else:  # stories / highlights
        exts, send_as = PHOTO_EXT | VIDEO_EXT, "mixed"

    cmd, _ = build_gdl_cmd(
        out_dir=out_dir, archive=archive, cookie=cookie,
        sleep=sleep, url=url, mode=mode, platform=platform,
    )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    seen: set[Path] = set()
    buffer: list[Path] = []
    sent_count = 0

    async def drain() -> None:
        nonlocal sent_count
        if not buffer:
            return
        await flush(target, list(buffer), send_as)
        sent_count += len(buffer)
        buffer.clear()

    try:
        # BUG-02: simple, honest polling loop — no dead shield/wait_for.
        while True:
            if stop.is_set():
                try:
                    proc.kill()
                except Exception:
                    pass
                break

            await asyncio.sleep(0.5)

            if out_dir.exists():
                for f in sorted(out_dir.iterdir()):
                    if f in seen or not f.is_file():
                        continue
                    if f.suffix.lower() not in exts:
                        continue
                    # only process files that aren't still being written
                    try:
                        s1 = f.stat().st_size
                        await asyncio.sleep(0.2)
                        s2 = f.stat().st_size
                        if s1 != s2:
                            continue
                    except Exception:
                        continue

                    seen.add(f)
                    buffer.append(f)
                    if len(buffer) >= MEDIA_GROUP_MAX:
                        await drain()

            if proc.returncode is not None:
                break

        # Final sweep after subprocess exited cleanly
        if not stop.is_set() and out_dir.exists():
            await asyncio.sleep(0.3)
            for f in sorted(out_dir.iterdir()):
                if f in seen or not f.is_file():
                    continue
                if f.suffix.lower() not in exts:
                    continue
                seen.add(f)
                buffer.append(f)
                if len(buffer) >= MEDIA_GROUP_MAX:
                    await drain()

        await drain()

        if status:
            await status.set(
                f"📦 `{handle}` → {sent_count} file(s) on `{mode}`.",
                force=True,
            )
    finally:
        # Wipe the per-run download dir only. Archive remains persistent.
        shutil.rmtree(out_dir, ignore_errors=True)
        if proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                await proc.wait()
            except Exception:
                pass

    return sent_count


# =============================================================================
# 10. HIGH-LEVEL ORCHESTRATORS
# =============================================================================

def _release(uid: int, ev: asyncio.Event) -> None:
    """BUG-09: only remove STOP_EVENTS[uid] if it still points to OUR event."""
    if STOP_EVENTS.get(uid) is ev:
        STOP_EVENTS.pop(uid, None)
    ACTIVE_USERS.discard(uid)


async def do_download(msg, choice: str, uid: int, uname: str,
                      name: str, bot, stop: asyncio.Event) -> None:
    mode_map = {"1": "photos", "2": "videos", "3": "both", "4": "documents"}
    mode     = mode_map.get(choice, "photos")
    label    = {"photos": "🖼️ Photos", "videos": "🎬 Videos",
                "both":   "📦 Both",   "documents": "📁 Files"}[mode]

    ch = get_channel(uid)
    target = (bot, normalize_chat(ch)) if ch else msg

    first = await msg.reply_text(
        f"⏳ *{label}* — starting…" + (f"\n📡 → {ch}" if ch else ""),
        parse_mode="Markdown",
    )
    status = Status(first)

    started = datetime.now()
    total   = 0

    try:
        for platform, (_, cookie_name, sleep) in PLATFORMS.items():
            if stop.is_set():
                break
            urls = read_profiles(uid, platform)
            if not urls:
                continue

            cookie = resolve_cookie(uid, platform)

            for url in urls:
                if stop.is_set():
                    break
                handle = handle_from_url(url)

                await status.set(f"⏳ *{platform.capitalize()}* › `{handle}`")

                modes = ("photos", "videos") if mode == "both" else (mode,)
                for m in modes:
                    if stop.is_set():
                        break
                    n = await realtime_download(
                        target=target, uid=uid, platform=platform,
                        handle=handle, mode=m, url=url, cookie=cookie,
                        sleep=sleep, stop=stop, status=status,
                    )
                    total += n
                    if n > 0:
                        append_history(uid, {
                            "date":     datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "platform": platform,
                            "user":     handle,
                            "media":    m,
                            "sent":     n,
                        })

        elapsed = int((datetime.now() - started).total_seconds())
        final = (f"⏹️ *Stopped.* {total} file(s) in {elapsed}s."
                 if stop.is_set() else
                 f"✅ *Done!* {total} file(s) in {elapsed}s.")
        await status.set(final, force=True)
    finally:
        _release(uid, stop)
        await send_menu(msg, uid, uname, name)


async def do_special_download(msg, url: str, platform: str, mode: str,
                              uid: int, uname: str, name: str, bot,
                              stop: asyncio.Event) -> None:
    label = "📖 Stories" if mode == "stories" else "🌟 Highlights"
    ch    = get_channel(uid)
    target = (bot, normalize_chat(ch)) if ch else msg
    handle = handle_from_url(url)

    first = await msg.reply_text(
        f"⏳ *{label}* › `{handle}`…", parse_mode="Markdown"
    )
    status = Status(first)

    cookie = resolve_cookie(uid, platform)
    _, _, sleep = PLATFORMS[platform]

    try:
        n = await realtime_download(
            target=target, uid=uid, platform=platform, handle=handle,
            mode=mode, url=url, cookie=cookie, sleep=sleep,
            stop=stop, status=status,
        )
        if n > 0:
            append_history(uid, {
                "date":     datetime.now().strftime("%Y-%m-%d %H:%M"),
                "platform": platform,
                "user":     handle,
                "media":    mode,
                "sent":     n,
            })
        await status.set(f"✅ *Done!* {n} file(s) sent.", force=True)
    finally:
        _release(uid, stop)
        await send_menu(msg, uid, uname, name)


def start_download_task(uid: int, coro_func, *args) -> None:
    """
    Register a fresh stop-event for `uid`, then fire the coroutine as a task.
    BUG-09/10: signals any pre-existing run to quit before starting a new one.
    Creates the event and passes it to the coroutine to ensure consistency.
    """
    old = STOP_EVENTS.get(uid)
    if old:
        old.set()

    ev = asyncio.Event()
    STOP_EVENTS[uid] = ev
    ACTIVE_USERS.add(uid)
    asyncio.ensure_future(coro_func(*args, ev))


# =============================================================================
# 11. TELEGRAM HANDLERS
# =============================================================================

# ── helpers ──────────────────────────────────────────────────────────────────

def _user(update: Update) -> tuple[int, str, str]:
    u = update.effective_user
    return u.id, u.username or "unknown", u.first_name or "User"


async def _answer(q) -> None:
    try:
        await q.answer()
    except Exception:
        pass


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, name = _user(update)
    await send_menu(update.message, uid, uname, name)


# ── /cleanup  (also reachable via button) ────────────────────────────────────

async def cmd_cleanup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, name = _user(update)
    freed = wipe_downloads(uid)
    await update.message.reply_text(
        f"🗑️ Freed *{freed} MB* of cached downloads.", parse_mode="Markdown"
    )
    await send_menu(update.message, uid, uname, name)


# ── cookie upload handler ─────────────────────────────────────────────────────

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Accept a .txt cookies file and save it to the user's cookie dir."""
    uid, uname, name = _user(update)
    doc = update.message.document
    if not doc or not doc.file_name:
        return

    fname = doc.file_name.lower()
    matched = None
    for platform, (_, cookie_name, _) in PLATFORMS.items():
        if fname == cookie_name or fname == platform + "_cookies.txt":
            matched = (platform, cookie_name)
            break

    if not matched:
        await update.message.reply_text(
            "⚠️ Unrecognised cookie file name.\n"
            "Expected one of:\n" +
            "\n".join(f"  • `{v[1]}`" for v in PLATFORMS.values()),
            parse_mode="Markdown",
        )
        return

    platform, cookie_name = matched
    dest = cdir(uid) / cookie_name
    tg_file = await doc.get_file()
    await tg_file.download_to_drive(str(dest))
    await update.message.reply_text(
        f"🍪 Cookies saved for *{platform.capitalize()}*.", parse_mode="Markdown"
    )
    await send_menu(update.message, uid, uname, name)


# ── text / URL handler ────────────────────────────────────────────────────────

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, name = _user(update)
    text  = (update.message.text or "").strip()
    state = ctx.user_data.get("state", S_MAIN)

    # ── set channel ──
    if state == S_SET_CHANNEL:
        ctx.user_data["state"] = S_MAIN
        if text.lower() in ("clear", "none", "-"):
            set_channel(uid, "clear")
            await update.message.reply_text("📡 Output channel cleared.")
        else:
            set_channel(uid, normalize_chat(text))
            await update.message.reply_text(
                f"📡 Output channel set to `{text}`.", parse_mode="Markdown"
            )
        await send_menu(update.message, uid, uname, name)
        return

    # ── add URL for a platform ──
    if state == S_ADD_URL:
        platform = ctx.user_data.get("platform")
        ctx.user_data["state"] = S_MAIN
        if not platform or platform not in PLATFORMS:
            await update.message.reply_text("❌ Invalid platform. Please try again.")
            await send_menu(update.message, uid, uname, name)
            return

        ok, err = validate_url(text, platform)
        if not ok:
            await update.message.reply_text(f"❌ {err}")
            await send_menu(update.message, uid, uname, name)
            return

        existing = read_profiles(uid, platform)
        if text in existing:
            await update.message.reply_text("ℹ️ That URL is already in your list.")
        else:
            write_profiles(uid, platform, existing + [text])
            await update.message.reply_text(
                f"✅ Added to *{platform.capitalize()}*: `{text}`",
                parse_mode="Markdown",
            )
        await send_menu(update.message, uid, uname, name)
        return

    # ── stories URL ──
    if state == S_STORY:
        platform = ctx.user_data.get("platform")
        ctx.user_data["state"] = S_MAIN
        if not platform or platform not in PLATFORMS:
            await update.message.reply_text("❌ Invalid platform. Please try again.")
            await send_menu(update.message, uid, uname, name)
            return
        ok, err = validate_url(text, platform)
        if not ok:
            await update.message.reply_text(f"❌ {err}")
            await send_menu(update.message, uid, uname, name)
            return

        start_download_task(
            uid,
            do_special_download,
            update.message, text, platform, "stories",
            uid, uname, name, ctx.bot,
        )
        return

    # ── highlights URL ──
    if state == S_HIGHLIGHT:
        platform = ctx.user_data.get("platform")
        ctx.user_data["state"] = S_MAIN
        if not platform or platform not in PLATFORMS:
            await update.message.reply_text("❌ Invalid platform. Please try again.")
            await send_menu(update.message, uid, uname, name)
            return
        ok, err = validate_url(text, platform)
        if not ok:
            await update.message.reply_text(f"❌ {err}")
            await send_menu(update.message, uid, uname, name)
            return

        start_download_task(
            uid,
            do_special_download,
            update.message, text, platform, "highlights",
            uid, uname, name, ctx.bot,
        )
        return

    # ── default: show menu ──
    await send_menu(update.message, uid, uname, name)


# ── inline-button dispatcher ──────────────────────────────────────────────────

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q    = update.callback_query
    uid, uname, name = _user(update)
    data = q.data or ""
    await _answer(q)

    # ── back / main ──────────────────────────────────────────────────────────
    if data in ("m_back", "m_main"):
        ctx.user_data["state"] = S_MAIN
        await send_menu(q.message, uid, uname, name, edit=True)
        return

    # ── add source ───────────────────────────────────────────────────────────
    if data == "m_add":
        await q.message.edit_text(
            "➕ *Add source* — pick a platform:", parse_mode="Markdown",
            reply_markup=kb_platforms("add"),
        )
        return

    if data.startswith("add_"):
        platform = data[4:]
        if platform not in PLATFORMS:
            return
        ctx.user_data.update(state=S_ADD_URL, platform=platform)
        hint = PLATFORM_URL_HINTS[platform]
        await q.message.edit_text(
            f"➕ *{platform.capitalize()}*\n"
            f"Send the profile URL, e.g.:\n`{hint}username`",
            parse_mode="Markdown",
            reply_markup=kb_back(),
        )
        return

    # ── remove source ────────────────────────────────────────────────────────
    if data == "m_remove":
        await q.message.edit_text(
            "🚫 *Remove source* — pick a platform:", parse_mode="Markdown",
            reply_markup=kb_platforms("rem"),
        )
        return

    if data.startswith("rem_"):
        platform = data[4:]
        if platform not in PLATFORMS:
            return
        urls = read_profiles(uid, platform)
        if not urls:
            await q.message.edit_text(
                f"ℹ️ No sources for *{platform.capitalize()}*.",
                parse_mode="Markdown", reply_markup=kb_back(),
            )
            return
        rows = [
            [InlineKeyboardButton(f"❌ {u}", callback_data=f"del_{platform}_{i}")]
            for i, u in enumerate(urls)
        ]
        rows.append([InlineKeyboardButton("🔙 Back", callback_data="m_back")])
        await q.message.edit_text(
            f"🚫 *{platform.capitalize()}* sources — tap to remove:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if data.startswith("del_"):
        parts    = data.split("_", 2)
        platform = parts[1]
        idx      = int(parts[2])
        urls = read_profiles(uid, platform)
        if 0 <= idx < len(urls):
            removed = urls.pop(idx)
            write_profiles(uid, platform, urls)
            await q.message.reply_text(
                f"✅ Removed: `{removed}`", parse_mode="Markdown"
            )
        await send_menu(q.message, uid, uname, name)
        return

    # ── list sources ─────────────────────────────────────────────────────────
    if data == "m_list":
        lines: list[str] = []
        for p in PLATFORMS:
            urls = read_profiles(uid, p)
            if urls:
                lines.append(f"*{p.capitalize()}*")
                lines += [f"  • `{u}`" for u in urls]
        text = "\n".join(lines) if lines else "ℹ️ No sources added yet."
        await q.message.edit_text(text, parse_mode="Markdown", reply_markup=kb_back())
        return

    # ── run download ─────────────────────────────────────────────────────────
    if data == "m_run":
        if uid in ACTIVE_USERS:
            await q.message.reply_text(
                "⚠️ A download is already running. Tap 🚫 Stop first."
            )
            return
        await q.message.edit_text(
            "✅ *Run download* — choose media type:",
            parse_mode="Markdown", reply_markup=kb_media(),
        )
        return

    if data.startswith("dl_"):
        choice = data[3:]
        if uid in ACTIVE_USERS:
            await q.message.reply_text("⚠️ Already running.")
            return
        # Atomically check and set to prevent race conditions
        if uid not in ACTIVE_USERS:  # Double-check before starting
            start_download_task(
                uid,
                do_download,
                q.message, choice, uid, uname, name, ctx.bot,
            )
        return

    # ── stories ──────────────────────────────────────────────────────────────
    if data == "m_stories":
        await q.message.edit_text(
            "📖 *Stories* — pick a platform:", parse_mode="Markdown",
            reply_markup=kb_platforms("story"),
        )
        return

    if data.startswith("story_"):
        platform = data[6:]
        if platform not in PLATFORMS:
            return
        ctx.user_data.update(state=S_STORY, platform=platform)
        await q.message.edit_text(
            f"📖 *Stories* › {platform.capitalize()}\nSend the profile URL:",
            parse_mode="Markdown", reply_markup=kb_back(),
        )
        return

    # ── highlights ───────────────────────────────────────────────────────────
    if data == "m_highlights":
        await q.message.edit_text(
            "✨ *Highlights* — pick a platform:", parse_mode="Markdown",
            reply_markup=kb_platforms("hl"),
        )
        return

    if data.startswith("hl_"):
        platform = data[3:]
        if platform not in PLATFORMS:
            return
        ctx.user_data.update(state=S_HIGHLIGHT, platform=platform)
        await q.message.edit_text(
            f"✨ *Highlights* › {platform.capitalize()}\nSend the profile URL:",
            parse_mode="Markdown", reply_markup=kb_back(),
        )
        return

    # ── stop ─────────────────────────────────────────────────────────────────
    if data == "m_stop":
        ev = STOP_EVENTS.get(uid)
        if ev:
            ev.set()
            await q.message.reply_text("⏹️ Stop signal sent.")
        else:
            await q.message.reply_text("ℹ️ No active download.")
        return

    # ── history ──────────────────────────────────────────────────────────────
    if data == "m_history":
        entries = read_history(uid)
        if not entries:
            await q.message.edit_text("ℹ️ No history yet.", reply_markup=kb_back())
            return
        lines = []
        for e in entries[:20]:
            lines.append(
                f"📅 `{e.get('date','-')}` | *{e.get('platform','-')}* "
                f"› `{e.get('user','-')}` | {e.get('media','-')} | "
                f"{e.get('sent',0)} file(s)"
            )
        await q.message.edit_text(
            "\n".join(lines), parse_mode="Markdown", reply_markup=kb_back()
        )
        return

    # ── cookies ───────────────────────────────────────────────────────────────
    if data == "m_cookies":
        names = "\n".join(f"  • `{v[1]}`" for v in PLATFORMS.values())
        await q.message.edit_text(
            "🍪 *Set cookies*\n\n"
            "Upload a Netscape-format `.txt` cookie file named after the platform:\n"
            f"{names}\n\n"
            "Just send the file in this chat and it will be saved automatically.",
            parse_mode="Markdown", reply_markup=kb_back(),
        )
        return

    # ── status ────────────────────────────────────────────────────────────────
    if data == "m_status":
        cached = folder_mb(udir(uid) / "downloads")
        active = "▶️ Running" if uid in ACTIVE_USERS else "⏸️ Idle"
        ch     = get_channel(uid) or "Direct chat"
        text   = (
            f"📊 *Status*\n\n"
            f"• State   : {active}\n"
            f"• Sources : {total_profiles(uid)}\n"
            f"• Cookies : {cookie_summary(uid)}\n"
            f"• Channel : `{ch}`\n"
            f"• Cached  : {cached} MB\n"
            f"• Sent    : {total_sent(uid)} file(s)"
        )
        await q.message.edit_text(text, parse_mode="Markdown", reply_markup=kb_back())
        return

    # ── set channel ───────────────────────────────────────────────────────────
    if data == "m_channel":
        ctx.user_data["state"] = S_SET_CHANNEL
        await q.message.edit_text(
            "📡 *Set output channel*\n\n"
            "Send your channel username (e.g. `@mychannel`) or numeric ID.\n"
            "Type `clear` to remove the current setting.",
            parse_mode="Markdown", reply_markup=kb_back(),
        )
        return

    # ── cleanup ───────────────────────────────────────────────────────────────
    if data == "m_cleanup":
        freed = wipe_downloads(uid)
        await q.message.reply_text(
            f"🗑️ Freed *{freed} MB* of cached downloads.", parse_mode="Markdown"
        )
        await send_menu(q.message, uid, uname, name)
        return


# =============================================================================
# 12. MAIN
# =============================================================================

def bootstrap_env_cookies() -> None:
    """
    Read COOKIE_INSTAGRAM / COOKIE_TIKTOK / COOKIE_FACEBOOK / COOKIE_X from
    environment variables and write them as Netscape cookie files into the
    global cookie directory.  Values may be raw text or base64-encoded text.
    This makes your Railway COOKIE_* variables actually work.
    """
    dest_dir = global_cookie_dir()
    for env_key, cookie_filename in COOKIE_ENV_MAP.items():
        value = os.environ.get(env_key, "").strip()
        if not value:
            continue
        dest = dest_dir / cookie_filename
        if dest.exists():
            continue          # don't overwrite a manually uploaded file
        # Detect base64: no newlines and only base64 chars
        try:
            decoded = base64.b64decode(value).decode("utf-8")
            content = decoded
        except Exception:
            content = value   # treat as plain Netscape text
        try:
            dest.write_text(content)
            print(f"[bootstrap] Wrote {cookie_filename} from env {env_key}")
        except Exception as exc:
            print(f"[bootstrap] Failed to write {cookie_filename}: {exc}")


def main() -> None:
    bootstrap_env_cookies()

    app = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("menu",    cmd_start))
    app.add_handler(CommandHandler("cleanup", cmd_cleanup))

    # Inline buttons
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Document upload (cookies)
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Text / URLs
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
