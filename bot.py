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
    ok = [p for p in PLATFORMS if (cdir(uid) / PLATFORMS[p][1]).exists()]
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
        # Channels / supergroups are -100 prefixed when numeric
        return n if n < 0 else int(f"-100{n}")
    return v  # let Telegram reject if it's garbage


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
    cached    = folder_mb(udir(uid) / "downloads")
    disk_line = f"\n💾 Cached: *{cached} MB*" if cached > 0 else ""
    return (
        f"@{username}, {name}\n"
        f"🪪 ID: `{uid}`\n"
        f"🆓 Free account\n"
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
            await msg.edit_text(text, reply_markup=kb_main(), parse_mode="Markdown")
        else:
            await msg.reply_text(text, reply_markup=kb_main(), parse_mode="Markdown")
    except BadRequest:
        try:
            await msg.reply_text(text, reply_markup=kb_main(), parse_mode="Markdown")
        except Exception:
            pass
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
                    if send_as == "photos":
                        group = [InputMediaPhoto(open(f, "rb")) for f in chunk]
                    elif send_as == "videos":
                        group = [InputMediaVideo(open(f, "rb")) for f in chunk]
                    else:  # mixed
                        group = [
                            InputMediaPhoto(open(f, "rb")) if file_kind(f) == "photo"
                            else InputMediaVideo(open(f, "rb"))
                            for f in chunk
                        ]
                    await _send_group(target, group)
                except Exception:
                    for f in chunk:
                        kind = {"photos": "photo", "videos": "video"}.get(send_as) or file_kind(f)
                        await _send_one(target, f, kind)
    finally:
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

            cookie = cdir(uid) / cookie_name

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

    _, cookie_name, sleep = PLATFORMS[platform]
    cookie = cdir(uid) / cookie_name

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


def start_download_task(uid: int, coro
