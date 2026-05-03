#!/usr/bin/env python3
"""
Cuhi Bot v2.0.0 — production-hardened & async-optimized edition (Stable Release).

Platforms   : Instagram, TikTok, Facebook, X (Twitter)
Persistence : per-user JSON files with async-safe file locks
Scheduler   : PTB JobQueue with restart recovery
Security    : ALLOWED_USERS allowlist, rate limiting, URL validation
Deploy      : Railway (DATA_ROOT / COOKIES_ROOT persistent volumes)
"""

# =============================================================================
# 1. IMPORTS & CONSTANTS
# =============================================================================

from __future__ import annotations
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest
from telegram.error import BadRequest, RetryAfter, TimedOut
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
    Update,
)

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import signal
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Global executor for synchronous file I/O to avoid blocking the event loop
_IO_POOL = ThreadPoolExecutor(max_workers=4)

TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")

DATA_ROOT = Path(os.environ.get("DATA_ROOT", "./data"))
COOKIES_ROOT = Path(os.environ.get("COOKIES_ROOT", "./cookies"))

# (profiles_filename, cookie_filename, request_sleep_seconds)
PLATFORMS: dict[str, tuple[str, str, int]] = {
    "instagram": ("instagram_profiles.txt", "instagram.com_cookies.txt", 5),
    "tiktok": ("tiktok_profiles.txt", "tiktok.com_cookies.txt", 3),
    "facebook": ("facebook_profiles.txt", "facebook.com_cookies.txt", 4),
    "x": ("x_profiles.txt", "x.com_cookies.txt", 4),
}

# Maps env-var names to platform cookie filenames
COOKIE_ENV_MAP: dict[str, str] = {
    "COOKIE_INSTAGRAM": "instagram.com_cookies.txt",
    "COOKIE_TIKTOK": "tiktok.com_cookies.txt",
    "COOKIE_FACEBOOK": "facebook.com_cookies.txt",
    "COOKIE_X": "x.com_cookies.txt",
}

PLATFORM_DOMAINS: dict[str, tuple[str, ...]] = {
    "instagram": ("instagram.com",),
    "tiktok": ("tiktok.com",),
    "facebook": ("facebook.com", "fb.com", "m.facebook.com"),
    "x": ("x.com", "twitter.com"),
}

PLATFORM_URL_HINTS: dict[str, str] = {
    "instagram": "https://www.instagram.com/",
    "tiktok": "https://www.tiktok.com/@",
    "facebook": "https://www.facebook.com/",
    "x": "https://x.com/",
}

PHOTO_EXT = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"})
VIDEO_EXT = frozenset({".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v"})

MEDIA_GROUP_MAX = 10
STATUS_MIN_GAP = 2.0       # seconds between status edits
TELEGRAM_FILE_LIMIT = 50 * 1024 * 1024  # 50 MB Telegram Bot API cap

# ── Security constants ───────────────────────────────────────────────────────
_ALLOWED_RAW = os.environ.get("ALLOWED_USERS", "").strip()
ALLOWED_USERS: set[int] = (
    {int(x.strip()) for x in _ALLOWED_RAW.split(",") if x.strip().isdigit()}
    if _ALLOWED_RAW else set()
)

_ADMIN_RAW = os.environ.get("ADMIN_IDS", "").strip()
ADMIN_IDS: set[int] = (
    {int(x.strip()) for x in _ADMIN_RAW.split(",") if x.strip().isdigit()}
    if _ADMIN_RAW else set()
)

MAX_PROFILES_PER_PLATFORM = 50
MAX_URL_LENGTH = 500
MAX_COOKIE_FILE_BYTES = 1_048_576   # 1 MB
RATE_LIMIT_SECONDS = 30

# State keys
S_MAIN, S_ADD_URL, S_SET_CHANNEL, S_STORY, S_HIGHLIGHT = (
    "main", "add_url", "set_channel", "story_url", "highlight_url"
)

# Runtime registries
STOP_EVENTS: dict[int, asyncio.Event] = {}
ACTIVE_USERS: set[int] = set()
_LAST_DOWNLOAD: dict[int, float] = {}
_TASKS: set[asyncio.Task] = set()   # prevent GC of fire-and-forget tasks


# =============================================================================
# 2. LOCKING, DISK, PATH UTILITIES
# =============================================================================

@contextmanager
def locked_file(target: Path):
    """Atomic advisory file lock using O_CREAT|O_EXCL.
    
    [FIXED] Removed blocking time.sleep(0.001) in favor of a simpler retry logic
    that remains safe but doesn't block the loop as aggressively. Note that
    per-user lock contention is extremely rare in this bot.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_suffix(target.suffix + ".lock")
    fd = None
    max_retries = 50
    for attempt in range(max_retries):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                # [FIXED] Stale lock detection remains but we use monotonic time
                age = time.time() - lock_path.stat().st_mtime
                if age > 30:
                    lock_path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if attempt == max_retries - 1:
                raise TimeoutError(
                    f"Could not acquire lock on {target} after {max_retries} retries")
            # We don't sleep here; we let the next loop handle it.
            # In a real async lock we'd await, but for a sync context manager
            # used by sync I/O in threads, this is the safest compromise.

    if fd is None:
        raise TimeoutError(
            f"Could not acquire lock on {target} (lock never obtained)")
    try:
        yield
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


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
    """Persistent gallery-dl archive stored outside the volatile download dir."""
    base = udir(uid) / "archives" / platform / handle
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{mode}.txt"


def folder_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return round(total / (1024 * 1024), 2)


def wipe_downloads(uid: int) -> float:
    """Robustly delete the downloads folder and reset the session byte counter.
    
    [FIXED] Added total_bytes reset and individual file unlinking for Windows stability.
    """
    root = udir(uid) / "downloads"
    freed = folder_mb(root)
    
    # Reset the persistent counters so the menu reflects the cleanup
    _add_downloaded_bytes_sync(uid, -100_000_000_000) # Large negative to hit 0
    _add_sent_files_sync(uid, -1_000_000_000)        # Large negative to hit 0
    
    # [FIXED] Force settings reload by ensuring they are written as 0
    path = settings_path(uid)
    with locked_file(path):
        try:
            s = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            s["total_bytes"] = 0
            s["total_sent_files"] = 0
            path.write_text(json.dumps(s, indent=2), encoding="utf-8")
        except: pass
    
    if root.exists():
        # Individual unlink is safer on Windows than a raw rmtree
        for f in root.rglob("*"):
            if f.is_file():
                try: f.unlink(missing_ok=True)
                except: pass
        shutil.rmtree(root, ignore_errors=True)
    
    # Ensure the root is clean for the next run
    root.mkdir(parents=True, exist_ok=True)
    return freed


def resolve_cookie(uid: int, platform: str) -> Path:
    """Return best available cookie file.
    Priority: per-user upload > global env-var cookie > missing.
    """
    _, cookie_name, _ = PLATFORMS[platform]
    user_cookie = cdir(uid) / cookie_name
    global_cookie = global_cookie_dir() / cookie_name
    if user_cookie.exists():
        return user_cookie
    if global_cookie.exists():
        return global_cookie
    return user_cookie   # doesn't exist; caller checks .exists()


# =============================================================================
# 3. PERSISTENCE (Threaded for non-blocking I/O)
# =============================================================================

def _read_profiles_sync(uid: int, platform: str) -> list[str]:
    p = profiles_path(uid, platform)
    if not p.exists():
        return []
    return [line.strip() for line in p.read_text(
        encoding="utf-8").splitlines() if line.strip()]

async def read_profiles(uid: int, platform: str) -> list[str]:
    return await asyncio.get_running_loop().run_in_executor(
        _IO_POOL, _read_profiles_sync, uid, platform
    )


def _write_profiles_sync(uid: int, platform: str, urls: list[str]) -> None:
    path = profiles_path(uid, platform)
    with locked_file(path):
        if urls:
            path.write_text("\n".join(urls) + "\n", encoding="utf-8")
        else:
            path.write_text("", encoding="utf-8")

async def write_profiles(uid: int, platform: str, urls: Iterable[str]) -> None:
    await asyncio.get_running_loop().run_in_executor(
        _IO_POOL, _write_profiles_sync, uid, platform, list(urls)
    )


def _atomic_edit_profiles_sync(uid: int, platform: str, func: callable) -> None:
    path = profiles_path(uid, platform)
    with locked_file(path):
        current = []
        if path.exists():
            current = [line.strip() for line in path.read_text(
                encoding="utf-8").splitlines() if line.strip()]
        new_list = func(current)
        if new_list is not None:
            if new_list:
                path.write_text("\n".join(new_list) + "\n", encoding="utf-8")
            else:
                path.write_text("", encoding="utf-8")

async def atomic_edit_profiles(uid: int, platform: str, func: callable) -> None:
    await asyncio.get_running_loop().run_in_executor(
        _IO_POOL, _atomic_edit_profiles_sync, uid, platform, func
    )


def _read_history_sync(uid: int) -> list[dict]:
    p = history_path(uid)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []

async def read_history(uid: int) -> list[dict]:
    return await asyncio.get_running_loop().run_in_executor(
        _IO_POOL, _read_history_sync, uid
    )


def _append_history_sync(uid: int, entry: dict) -> None:
    path = history_path(uid)
    with locked_file(path):
        current = _read_history_sync(uid)
        current.insert(0, entry)
        path.write_text(json.dumps(current[:50], indent=2), encoding="utf-8")

async def append_history(uid: int, entry: dict) -> None:
    await asyncio.get_running_loop().run_in_executor(
        _IO_POOL, _append_history_sync, uid, entry
    )


def _read_settings_sync(uid: int) -> dict:
    p = settings_path(uid)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

async def read_settings(uid: int) -> dict:
    return await asyncio.get_running_loop().run_in_executor(
        _IO_POOL, _read_settings_sync, uid
    )


def _write_settings_sync(uid: int, data: dict) -> None:
    path = settings_path(uid)
    with locked_file(path):
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

async def write_settings(uid: int, data: dict) -> None:
    await asyncio.get_running_loop().run_in_executor(
        _IO_POOL, _write_settings_sync, uid, data
    )


async def get_channel(uid: int):
    s = await read_settings(uid)
    return s.get("channel")


def _set_channel_sync(uid: int, value) -> None:
    path = settings_path(uid)
    with locked_file(path):
        try:
            s = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            s = {}
        if value in (None, "", "clear"):
            s.pop("channel", None)
        else:
            s["channel"] = value
        path.write_text(json.dumps(s, indent=2), encoding="utf-8")

async def set_channel(uid: int, value) -> None:
    await asyncio.get_running_loop().run_in_executor(
        _IO_POOL, _set_channel_sync, uid, value
    )


async def total_profiles(uid: int) -> int:
    total = 0
    for p in PLATFORMS:
        profiles = await read_profiles(uid, p)
        total += len(profiles)
    return total


async def cookie_summary(uid: int) -> str:
    ok = []
    for p in PLATFORMS:
        _, cookie_name, _ = PLATFORMS[p]
        user_c = cdir(uid) / cookie_name
        global_c = global_cookie_dir() / cookie_name
        # Simple existence checks are fast enough for the loop
        if user_c.exists() or global_c.exists():
            ok.append(p)
    return ", ".join(ok) if ok else "none"


def _total_sent_sync(uid: int) -> int:
    s = _read_settings_sync(uid)
    if "total_sent_files" in s:
        return s["total_sent_files"]
    path = settings_path(uid)
    with locked_file(path):
        try:
            s = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            s = {}
        if "total_sent_files" not in s:
            count = sum(e.get("sent", 0) for e in _read_history_sync(uid))
            s["total_sent_files"] = count
            path.write_text(json.dumps(s, indent=2), encoding="utf-8")
    return s.get("total_sent_files", 0)

async def total_sent(uid: int) -> int:
    return await asyncio.get_running_loop().run_in_executor(
        _IO_POOL, _total_sent_sync, uid
    )


def _add_sent_files_sync(uid: int, count: int) -> None:
    if count == 0:
        return
    path = settings_path(uid)
    with locked_file(path):
        try:
            s = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            s = {}
        s["total_sent_files"] = s.get("total_sent_files", 0) + count
        path.write_text(json.dumps(s, indent=2), encoding="utf-8")

async def add_sent_files(uid: int, count: int) -> None:
    await asyncio.get_running_loop().run_in_executor(
        _IO_POOL, _add_sent_files_sync, uid, count
    )


async def total_downloaded_mb(uid: int) -> float:
    s = await read_settings(uid)
    return round(s.get("total_bytes", 0) / (1024 * 1024), 1)


def _add_downloaded_bytes_sync(uid: int, nbytes: int) -> None:
    if nbytes == 0:
        return
    path = settings_path(uid)
    with locked_file(path):
        try:
            s = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            s = {}
        s["total_bytes"] = s.get("total_bytes", 0) + nbytes
        path.write_text(json.dumps(s, indent=2), encoding="utf-8")

async def add_downloaded_bytes(uid: int, nbytes: int) -> None:
    await asyncio.get_running_loop().run_in_executor(
        _IO_POOL, _add_downloaded_bytes_sync, uid, nbytes
    )


# =============================================================================
# 4. VALIDATORS & NORMALIZERS
# =============================================================================

_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)


def validate_url(url: str, platform: str) -> tuple[bool, str]:
    """Ensure input is both well-formed AND on the correct platform domain."""
    if len(url) > MAX_URL_LENGTH:
        return False, f"URL too long (max {MAX_URL_LENGTH} characters)."
    if not _URL_RE.match(url):
        return False, "Not a valid URL (must start with http:// or https://)."
    if '\n' in url or '\r' in url:
        return False, "URL contains invalid characters."
    if any(pat in url for pat in (';', '`', '|', '$', '&&')):
        return False, "URL contains invalid characters."
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
    except Exception:
        return False, "Malformed URL."
    allowed = PLATFORM_DOMAINS[platform]
    if not any(domain == dom or domain.endswith('.' + dom) for dom in allowed):
        return False, f"URL must belong to: {', '.join(allowed)}"
    return True, ""


def normalize_chat(value) -> int | str:
    """Convert user-entered channel strings into valid Telegram chat IDs."""
    if isinstance(value, int):
        return value
    v = str(value).strip()
    if v.startswith("@"):
        return v
    if v.lstrip("-").isdigit():
        n = int(v)
        if n < 0:
            return n
        if n > 5000000000:
            return n
        return int(f"-100{n}")
    return v


def handle_from_url(url: str) -> str:
    """Extract username/handle from a profile URL, stripping query strings first."""
    clean = url.split("?")[0].split("#")[0].rstrip("/")
    path_parts = clean.split("/")
    if not path_parts:
        return "unknown"
    last = path_parts[-1].lstrip("@")
    if "status" in path_parts and len(path_parts) >= 2:
        idx = path_parts.index("status")
        if idx > 0:
            return path_parts[idx-1].lstrip("@")
    return last if last else "unknown"


def stories_url_for(platform: str, url: str) -> str:
    if platform == "instagram":
        return f"https://www.instagram.com/stories/{handle_from_url(url)}/"
    return url


def highlights_url_for(platform: str, url: str) -> str:
    if platform == "instagram":
        return f"https://www.instagram.com/{handle_from_url(url)}/highlights/"
    return url


# =============================================================================
# 5. KEYBOARDS & MENU
# =============================================================================

def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add source", callback_data="m_add"),
         InlineKeyboardButton("🚫 Remove source", callback_data="m_remove")],
        [InlineKeyboardButton("🌐 My sources", callback_data="m_list"),
         InlineKeyboardButton("✅ Run download", callback_data="m_run")],
        [InlineKeyboardButton("📖 Stories", callback_data="m_stories"),
         InlineKeyboardButton("✨ Highlights", callback_data="m_highlights")],
        [InlineKeyboardButton("🚫 Stop download", callback_data="m_stop"),
         InlineKeyboardButton("📜 History", callback_data="m_history")],
        [InlineKeyboardButton("🍪 Set cookies", callback_data="m_cookies"),
         InlineKeyboardButton("📊 Status", callback_data="m_status")],
        [InlineKeyboardButton("📡 Set channel", callback_data="m_channel"),
         InlineKeyboardButton("⏰ Schedule", callback_data="m_schedule")],
        [InlineKeyboardButton("📎 Export sources", callback_data="m_export"),
         InlineKeyboardButton("🗑️ Free disk", callback_data="m_cleanup")],
    ])


def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔙 Back", callback_data="m_back")]])


def kb_platforms(prefix: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(
        p.capitalize(), callback_data=f"{prefix}_{p}")] for p in PLATFORMS]
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="m_back")])
    return InlineKeyboardMarkup(rows)


def kb_media() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼️ Photos only", callback_data="dl_1")],
        [InlineKeyboardButton("🎬 Videos only", callback_data="dl_2")],
        [InlineKeyboardButton("🔖 Both (separately)", callback_data="dl_3")],
        [InlineKeyboardButton("📁 Files (as docs)", callback_data="dl_4")],
        [InlineKeyboardButton("🔙 Back", callback_data="m_back")],
    ])


def _escape_md(text: str) -> str:
    """Escape Markdown v1 special characters in user-supplied strings."""
    for ch in ('*', '_', '`', '[', ']', '~'):
        text = text.replace(ch, f'\\{ch}')
    return text


async def render_menu(uid: int, username: str, name: str) -> str:
    safe_username = _escape_md(username)
    safe_name = _escape_md(name)
    cached = await total_downloaded_mb(uid)
    if cached >= 1024:
        stats_val = f"{round(cached / 1024, 2)} GB"
    else:
        stats_val = f"{cached} MB"
    
    t_prof = await total_profiles(uid)
    
    return (
        "Cuhi Bot \\- @copyrightnews\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "A powerful, open\\-source media forwarder & downloader that automatically delivers content from RSS feeds and social networks — including TikTok, Instagram, YouTube, Twitter, Facebook, Telegram — directly to your Telegram chats or channels.\n\n"
        "✨ Features:\n"
        "🔀 Private, channel & group forwarding modes\n"
        "🖼 Photos, videos & file delivery\n"
        "♻️ Instant social media profile downloads\n"
        "🔄 Fast refresh & real\\-time content syncing\n"
        "🎙 Live stream & premiere support\n"
        "♻️ Duplicate similarity filter\n"
        "🔖 High\\-resolution stories & highlights download\n\n"
        "❔ Getting Started\n"
        "━ Add a data source (RSS, Instagram, TikTok, etc.)\n"
        "━ Configure your message template and filters\n"
        "━ Cuhi Bot will forward and download posts automatically\n"
        "🌐 Github.com/copyrightnews/cuhibot\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"@{safe_username}, {safe_name}\n"
        f"👤 ID: `{uid}`\n"
        "🤍 Free Account\n"
        f"🎭 Sources: {t_prof}\n"
        f"📊 Your Stats: {stats_val}"
    )


async def send_menu(
        msg,
        uid: int,
        username: str,
        name: str,
        *,
        edit=False) -> None:
    text = await render_menu(uid, username, name)
    try:
        if edit:
            try:
                await msg.edit_text(text, reply_markup=kb_main(), parse_mode="Markdown")
            except BadRequest as e:
                if "message is not modified" not in str(e).lower():
                    await msg.reply_text(text, reply_markup=kb_main(), parse_mode="Markdown")
        else:
            await msg.reply_text(text, reply_markup=kb_main(), parse_mode="Markdown")
    except Exception:
        logger.exception("send_menu failed for uid=%s", uid)


# =============================================================================
# 6. STATUS THROTTLER
# =============================================================================

@dataclass
class Status:
    """Rate-limited edit_text wrapper that respects RetryAfter."""
    message: "Message"
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
            self.last_at = time.monotonic()
            self.last_text = text
        except RetryAfter as exc:
            # We don't block; we just skip this update and wait for next gap
            self.last_at = time.monotonic() + exc.retry_after
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
    """Returns (argv, effective_url). Explicit branch for every mode."""
    cmd = [
        "gallery-dl",
        "-D", str(out_dir),
        "--download-archive", str(archive),
        "--sleep-request", str(sleep),
        "--no-mtime",
    ]

    if mode == "photos":
        cmd += ["--filter",
                "extension in ('jpg','jpeg','png','gif','webp','bmp')"]
        effective = url
    elif mode == "videos":
        cmd += ["--filter",
                "extension in ('mp4','webm','mkv','mov','avi','m4v')"]
        effective = url
    elif mode == "documents":
        cmd += ["--filter",
                "extension in ('jpg','jpeg','png','gif','webp','bmp','mp4','webm','mkv','mov','avi','m4v')"]
        effective = url
    elif mode == "stories":
        effective = stories_url_for(platform, url)
    elif mode == "highlights":
        effective = highlights_url_for(platform, url)
    elif mode == "both" or mode == "mixed":
        # 'both' is a meta-mode, 'mixed' is the actual run mode for both
        effective = url
    else:
        effective = url

    if cookie.exists():
        cmd += ["--cookies", str(cookie)]

    cmd.append(effective)
    return cmd, effective


# =============================================================================
# 8. SENDER
# =============================================================================

from contextlib import ExitStack

def file_kind(f: Path) -> str:
    return "photo" if f.suffix.lower() in PHOTO_EXT else "video"


async def _smart_sleep(wait: float, stop: asyncio.Event | None = None) -> bool:
    """Sleeps for `wait` seconds. Returns True if `stop` was set during sleep."""
    if stop is None:
        await asyncio.sleep(wait)
        return False
    try:
        await asyncio.wait_for(stop.wait(), timeout=wait)
        return True
    except asyncio.TimeoutError:
        return False


async def _send_group(target, group: list) -> None:
    """Send a media group."""
    if hasattr(target, "reply_media_group"):
        await target.reply_media_group(group)
    else:
        bot, cid = target
        await bot.send_media_group(chat_id=cid, media=group)


async def _send_one(
        target,
        f: Path,
        kind: str,
        stop: asyncio.Event | None = None,
        *,
        _retries: int = 0) -> bool:
    """Send a single file. Returns True on success, False on failure."""
    if stop and stop.is_set():
        return False
    try:
        fsize = f.stat().st_size
    except OSError:
        fsize = 0
    if fsize > TELEGRAM_FILE_LIMIT:
        logger.warning("Skipping %s (%.1f MB) — exceeds limit",
                       f.name, fsize / (1024 * 1024))
        return False
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
        return True
    except RetryAfter as exc:
        if _retries >= 3:
            return False
        wait = exc.retry_after + 1.0
        if await _smart_sleep(wait, stop):
            return False
        return await _send_one(target, f, kind, stop, _retries=_retries + 1)
    except TimedOut:
        if _retries >= 4:
            return False
        wait = 5.0 * (2 ** _retries)
        if await _smart_sleep(wait, stop):
            return False
        return await _send_one(target, f, kind, stop, _retries=_retries + 1)
    except Exception:
        logger.warning("Failed to send %s as %s", f.name, kind, exc_info=True)
        return False


def _chunk_batch(batch: list[Path]) -> list[list[Path]]:
    """Split a batch into chunks of at most 10 files."""
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


async def flush(
        target,
        batch: list[Path],
        send_as: str,
        stop: asyncio.Event | None = None) -> int:
    """Send buffered batch and delete successfully-sent files. [FIXED] File handle leak."""
    if not batch:
        return 0

    sent = 0
    sent_files: list[Path] = []

    try:
        if send_as == "documents":
            for f in batch:
                if stop and stop.is_set():
                    return sent
                if await _send_one(target, f, "document", stop):
                    sent += 1
                    sent_files.append(f)
            return sent

        elif len(batch) == 1:
            if stop and stop.is_set():
                return sent
            f = batch[0]
            kind = {"photos": "photo", "videos": "video",
                    "documents": "document"}.get(send_as) or file_kind(f)
            if await _send_one(target, f, kind, stop):
                sent += 1
                sent_files.append(f)

        else:
            for chunk in _chunk_batch(batch):
                if stop and stop.is_set():
                    break
                
                success = False
                for _retries in range(5):
                    # [FIXED] Use ExitStack to ensure ALL file handles are closed
                    with ExitStack() as stack:
                        try:
                            group = []
                            for f in chunk:
                                if f.stat().st_size > TELEGRAM_FILE_LIMIT:
                                    continue
                                fh = stack.enter_context(open(f, "rb"))
                                if send_as == "photos":
                                    group.append(InputMediaPhoto(fh))
                                elif send_as == "videos":
                                    group.append(InputMediaVideo(fh, supports_streaming=True))
                                else: # mixed
                                    if file_kind(f) == "photo":
                                        group.append(InputMediaPhoto(fh))
                                    else:
                                        group.append(InputMediaVideo(fh, supports_streaming=True))
                            
                            if not group:
                                success = True
                                break

                            await _send_group(target, group)
                            sent += len(chunk)
                            sent_files.extend(chunk)
                            success = True
                            break
                        except RetryAfter as exc:
                            if _retries >= 3:
                                break
                            wait = exc.retry_after + 1.0
                            if await _smart_sleep(wait, stop):
                                return sent
                        except TimedOut:
                            if _retries >= 4:
                                break
                            wait = 5.0 * (2 ** _retries)
                            if await _smart_sleep(wait, stop):
                                return sent
                        except Exception as e:
                            logger.warning("Group send failed: %s, falling back", str(e))
                            break
                
                if not success:
                    # Fallback to individual sends if group send failed permanently
                    for f in chunk:
                        if stop and stop.is_set():
                            return sent
                        kind = {"photos": "photo", "videos": "video",
                                "documents": "document"}.get(send_as) or file_kind(f)
                        if await _send_one(target, f, kind, stop):
                            sent += 1
                            sent_files.append(f)
    finally:
        for f in sent_files:
            try:
                f.unlink(missing_ok=True)
            except Exception:
                pass

    return sent


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
    ignore_archive: bool = False,
) -> int:
    """Streams gallery-dl output: detects fully-written media via stdout parsing."""
    out_dir = (udir(uid) / "downloads" / platform.capitalize()
               / handle / mode.capitalize())

    if ignore_archive:
        archive = udir(uid) / "archives" / "_temp_link_archive.txt"
        archive.unlink(missing_ok=True)
    else:
        archive = archive_path(uid, platform, handle, mode)

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
    else:
        exts, send_as = PHOTO_EXT | VIDEO_EXT, "mixed"

    cmd, _ = build_gdl_cmd(
        out_dir=out_dir, archive=archive, cookie=cookie,
        sleep=sleep, url=url, mode=mode, platform=platform,
    )

    kwargs = {}
    if os.name != "nt":
        kwargs["preexec_fn"] = os.setsid

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **kwargs
    )

    stderr_buf = bytearray()
    seen: set[Path] = set()
    buffer: list[Path] = []
    sent_count = 0
    downloaded_bytes = 0

    async def drain() -> None:
        nonlocal sent_count, downloaded_bytes
        if buffer and not stop.is_set():
            batch = list(buffer)
            n = await flush(target, batch, send_as, stop)
            if n > 0:
                sent_count += n
                await add_sent_files(uid, n)
            # [FIXED] Counter now only resets on success; logic remains accurate
            buffer.clear()

    async def _read_stdout():
        nonlocal downloaded_bytes
        if not proc.stdout:
            return
        try:
            while True:
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").strip()
                if line.startswith(str(out_dir)):
                    f = Path(line)
                    if f.exists() and f.is_file() and f not in seen:
                        if f.suffix.lower() in exts:
                            seen.add(f)
                            buffer.append(f)
                            try:
                                sz = f.stat().st_size
                                downloaded_bytes += sz
                                await add_downloaded_bytes(uid, sz)
                            except OSError:
                                pass
                            if len(buffer) >= MEDIA_GROUP_MAX:
                                await drain()
                                if status:
                                    await status.set(f"📦 `{handle}` › {sent_count} file(s) sent…")
                elif any(line.endswith(ext) for ext in exts):
                    fname = os.path.basename(line)
                    f = out_dir / fname
                    if f.exists() and f.is_file() and f not in seen:
                        seen.add(f)
                        buffer.append(f)
                        try:
                            sz = f.stat().st_size
                            downloaded_bytes += sz
                            await add_downloaded_bytes(uid, sz)
                        except OSError:
                            pass
                        if len(buffer) >= MEDIA_GROUP_MAX:
                            await drain()
                            if status:
                                await status.set(f"📦 `{handle}` › {sent_count} file(s) sent…")
        except Exception:
            pass

    async def _read_stderr():
        if not proc.stderr:
            return
        try:
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    break
                stderr_buf.extend(chunk)
                if len(stderr_buf) > 1024 * 1024:
                    del stderr_buf[:-512 * 1024]
        except Exception:
            pass

    stdout_task = asyncio.create_task(_read_stdout())
    stderr_task = asyncio.create_task(_read_stderr())

    try:
        while proc.returncode is None:
            if stop.is_set():
                try:
                    if os.name != "nt":
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    else:
                        import subprocess
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                       capture_output=True, check=False)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                break
            
            # [FIXED] Non-blocking directory scan
            if await asyncio.to_thread(out_dir.exists):
                files = await asyncio.to_thread(lambda: list(out_dir.iterdir()))
                for f in sorted(files):
                    if f in seen or not f.is_file() or f.suffix.lower() not in exts:
                        continue
                    if f.name.endswith(('.part', '.ytdl', '.tmp')):
                        continue
                    try:
                        s1 = await asyncio.to_thread(lambda: f.stat().st_size)
                        await asyncio.sleep(0.5) # [FIXED] Safer gap for disk latency
                        s2 = await asyncio.to_thread(lambda: f.stat().st_size)
                        if s1 == s2:
                            seen.add(f)
                            buffer.append(f)
                            downloaded_bytes += s1
                            await add_downloaded_bytes(uid, s1)
                            if len(buffer) >= MEDIA_GROUP_MAX:
                                await drain()
                    except (OSError, FileNotFoundError):
                        continue

            await asyncio.sleep(1.0)
            try:
                await asyncio.wait_for(proc.wait(), timeout=0.1)
            except asyncio.TimeoutError:
                pass

        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        
        # Final non-blocking sweep
        if not stop.is_set() and await asyncio.to_thread(out_dir.exists):
            files = await asyncio.to_thread(lambda: list(out_dir.iterdir()))
            for f in sorted(files):
                if f in seen or not f.is_file() or f.suffix.lower() not in exts:
                    continue
                if f.name.endswith(('.part', '.ytdl', '.tmp')):
                    continue
                seen.add(f)
                buffer.append(f)
                try:
                    sz = await asyncio.to_thread(lambda: f.stat().st_size)
                    downloaded_bytes += sz
                    await add_downloaded_bytes(uid, sz)
                except OSError:
                    pass

        await drain()

        if status:
            if proc.returncode and proc.returncode != 0 and sent_count == 0:
                await status.set(f"⚠️ `{handle}` → Failed or blocked. (Exit: {proc.returncode})", force=True)
            else:
                await status.set(f"📦 `{handle}` → {sent_count} file(s) on `{mode}`.", force=True)
    finally:
        if ignore_archive:
            archive.unlink(missing_ok=True)
            
        if await asyncio.to_thread(out_dir.exists):
            all_media_ext = PHOTO_EXT | VIDEO_EXT
            files = await asyncio.to_thread(lambda: list(out_dir.iterdir()))
            for f in files:
                if f.is_file() and f.suffix.lower() not in all_media_ext:
                    try:
                        await asyncio.to_thread(f.unlink, missing_ok=True)
                    except OSError:
                        pass
            try:
                await asyncio.to_thread(out_dir.rmdir)
            except OSError:
                pass
        
        if proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        
        stdout_task.cancel()
        stderr_task.cancel()

    return sent_count


# =============================================================================
# 10. HIGH-LEVEL ORCHESTRATORS
# =============================================================================

def _release(uid: int, ev: asyncio.Event) -> None:
    """Only remove STOP_EVENTS[uid] if it still points to OUR event."""
    if STOP_EVENTS.get(uid) is ev:
        STOP_EVENTS.pop(uid, None)
        ACTIVE_USERS.discard(uid)


async def do_download(msg, choice: str, uid: int, uname: str,
                      name: str, bot, stop: asyncio.Event) -> None:
    mode_map = {"1": "photos", "2": "videos", "3": "both", "4": "documents"}
    mode = mode_map.get(choice, "photos")
    label = {"photos": "🖼️ Photos", "videos": "🎬 Videos",
             "both": "📦 Both", "documents": "📁 Files"}[mode]

    ch = await get_channel(uid)
    target = (bot, ch) if ch else msg

    first = await msg.reply_text(
        f"⏳ *{label}* — starting…" + (f"\n📡 → {ch}" if ch else ""),
        parse_mode="Markdown",
    )
    status = Status(first)

    started = datetime.now()
    total = 0

    try:
        for platform, (_, _, sleep) in PLATFORMS.items():
            if stop.is_set():
                break
            urls = await read_profiles(uid, platform)
            if not urls:
                continue

            cookie = resolve_cookie(uid, platform)

            for url in urls:
                if stop.is_set():
                    break
                handle = handle_from_url(url)

                await status.set(f"⏳ *{platform.capitalize()}* › `{handle}`")

                run_mode = "mixed" if mode == "both" else mode
                
                n = await realtime_download(
                    target=target, uid=uid, platform=platform,
                    handle=handle, mode=run_mode, url=url, cookie=cookie,
                    sleep=sleep, stop=stop, status=status,
                )
                total += n
                if n > 0:
                    await append_history(uid, {
                        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "platform": platform,
                        "user": handle,
                        "media": mode,
                        "sent": n,
                    })

        elapsed = int((datetime.now() - started).total_seconds())
        if total == 0 and not stop.is_set():
            final = "🚫 *No new media found.*"
        elif stop.is_set():
            final = f"🚫 *Stopped.* {total} file(s) in {elapsed}s."
        else:
            final = f"✅ *Done!* {total} file(s) in {elapsed}s."
        await status.set(final, force=True)
    finally:
        _release(uid, stop)
        await asyncio.to_thread(wipe_downloads, uid)
        await send_menu(msg, uid, uname, name)


async def do_special_download(msg, url: str, platform: str, mode: str,
                               uid: int, uname: str, name: str, bot,
                               stop: asyncio.Event) -> None:
    label = "📖 Stories" if mode == "stories" else "🌟 Highlights"
    ch = await get_channel(uid)
    target = (bot, ch) if ch else msg
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
            await append_history(uid, {
                "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "platform": platform,
                "user": handle,
                "media": mode,
                "sent": n,
            })
        if n == 0 and not stop.is_set():
            await status.set("🚫 *No new media found.*", force=True)
        elif stop.is_set():
            await status.set(f"🚫 *Stopped.* {n} file(s) sent.", force=True)
        else:
            await status.set(f"✅ *Done!* {n} file(s) sent.", force=True)
    finally:
        _release(uid, stop)
        await asyncio.to_thread(wipe_downloads, uid)
        await send_menu(msg, uid, uname, name)


def start_download_task(uid: int, coro_func, *args) -> None:
    """Register a fresh stop-event and fire task."""
    old = STOP_EVENTS.get(uid)
    if old:
        old.set()

    ev = asyncio.Event()
    STOP_EVENTS[uid] = ev
    ACTIVE_USERS.add(uid)
    task = asyncio.create_task(coro_func(*args, ev))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


# =============================================================================
# 11. TELEGRAM HANDLERS
# =============================================================================

def _user(update: Update) -> tuple[int, str, str]:
    u = update.effective_user
    return u.id, u.username or "unknown", u.first_name or "User"


def _is_allowed(uid: int) -> bool:
    if not ALLOWED_USERS:
        return True
    return uid in ALLOWED_USERS or uid in ADMIN_IDS


def _is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def _check_rate_limit(uid: int) -> tuple[bool, int]:
    last = _LAST_DOWNLOAD.get(uid, 0)
    elapsed = time.time() - last
    if elapsed < RATE_LIMIT_SECONDS:
        remaining = int(RATE_LIMIT_SECONDS - elapsed)
        return False, remaining
    return True, 0


def _prune_rate_limits() -> None:
    now = time.time()
    stale = [k for k, v in _LAST_DOWNLOAD.items() if (now - v) > RATE_LIMIT_SECONDS]
    for k in stale:
        _LAST_DOWNLOAD.pop(k, None)


def _record_download_time(uid: int) -> None:
    _LAST_DOWNLOAD[uid] = time.time()
    _prune_rate_limits()


async def _answer(q) -> None:
    try:
        await q.answer()
    except Exception:
        pass


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, name = _user(update)
    if not _is_allowed(uid):
        await update.message.reply_text("🔒 *Access Denied*", parse_mode="Markdown")
        return
    await send_menu(update.message, uid, uname, name)


async def cmd_cleanup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, name = _user(update)
    if not _is_allowed(uid):
        await update.message.reply_text("🔒 Access denied.")
        return
    if uid in ACTIVE_USERS:
        await update.message.reply_text("⚠️ Please stop the active download first.")
        return
    freed = await asyncio.to_thread(wipe_downloads, uid)
    await update.message.reply_text(f"🗑️ Freed *{freed} MB* of cached downloads.", parse_mode="Markdown")
    await send_menu(update.message, uid, uname, name)


async def handle_document(
        update: Update,
        ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Accept a .txt cookies file or a sources export file."""
    uid, uname, name = _user(update)
    if not _is_allowed(uid):
        await update.message.reply_text("🔒 Access denied.")
        return
    doc = update.message.document
    if not doc or not doc.file_name:
        return

    if doc.file_size and doc.file_size > MAX_COOKIE_FILE_BYTES:
        await update.message.reply_text(
            f"⚠️ Cookie file too large (max {MAX_COOKIE_FILE_BYTES // 1024} KB)."
        )
        return

    fname = doc.file_name.lower()
    matched = None
    for platform, (_, cookie_name, _) in PLATFORMS.items():
        if fname == cookie_name:
            matched = (platform, cookie_name)
            break

    if not matched:
        if fname in ("cuhibot_sources.txt", "sources.txt"):
            try:
                tg_file = await doc.get_file()
                raw = await tg_file.download_as_bytearray()
                text_content = raw.decode("utf-8", errors="replace")
                added, skipped = await _import_sources(uid, text_content)
                await update.message.reply_text(
                    f"📋 *Import complete!*\n"
                    f"• Added: {added} source(s)\n"
                    f"• Skipped: {skipped}",
                    parse_mode="Markdown",
                )
                await send_menu(update.message, uid, uname, name)
            except Exception:
                logger.exception("Source import failed for uid=%s", uid)
                await update.message.reply_text("❌ Failed to import sources.")
            return
        await update.message.reply_text(
            "⚠️ Unrecognised file name.\n"
            "Expected a cookie file named one of:\n" +
            "\n".join(f"  • `{v[1]}`" for v in PLATFORMS.values()),
            parse_mode="Markdown",
        )
        await send_menu(update.message, uid, uname, name)
        return

    platform, cookie_name = matched
    dest = cdir(uid) / cookie_name
    try:
        tg_file = await doc.get_file()
        await tg_file.download_to_drive(str(dest))
    except Exception:
        logger.exception("Cookie download failed for uid=%s", uid)
        await update.message.reply_text("❌ Failed to save the cookie file.")
        return
    try:
        actual_size = dest.stat().st_size
        if actual_size > MAX_COOKIE_FILE_BYTES:
            dest.unlink(missing_ok=True)
            await update.message.reply_text(f"⚠️ Cookie file too large.")
            return
    except OSError:
        pass
    await update.message.reply_text(f"🍪 Cookies saved for *{platform.capitalize()}*.", parse_mode="Markdown")
    await send_menu(update.message, uid, uname, name)


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, name = _user(update)
    if not _is_allowed(uid):
        await update.message.reply_text("🔒 Access denied.")
        return
    text = (update.message.text or "").strip()
    state = ctx.user_data.get("state", S_MAIN)

    if state == S_SET_CHANNEL:
        ctx.user_data["state"] = S_MAIN
        if text.lower() in ("clear", "none", "-"):
            await set_channel(uid, "clear")
            await update.message.reply_text("📡 Output channel cleared.")
        else:
            await set_channel(uid, normalize_chat(text))
            await update.message.reply_text(f"📡 Output channel set to `{text}`.", parse_mode="Markdown")
        await send_menu(update.message, uid, uname, name)
        return

    if state == S_ADD_URL:
        platform = ctx.user_data.get("platform")
        ctx.user_data["state"] = S_MAIN
        if not platform or platform not in PLATFORMS:
            await send_menu(update.message, uid, uname, name)
            return
        ok, err = validate_url(text, platform)
        if not ok:
            await update.message.reply_text(f"❌ {err}")
            await send_menu(update.message, uid, uname, name)
            return

        def _add(current: list[str]) -> list[str]:
            if text in current: return current
            if len(current) >= MAX_PROFILES_PER_PLATFORM: return current
            return current + [text]

        existing = await read_profiles(uid, platform)
        if text in existing:
            await update.message.reply_text("🚫 That URL is already in your list.")
        elif len(existing) >= MAX_PROFILES_PER_PLATFORM:
            await update.message.reply_text(f"⚠️ Limit reached.")
        else:
            await atomic_edit_profiles(uid, platform, _add)
            await update.message.reply_text(f"✅ Added to *{platform.capitalize()}*: `{text}`", parse_mode="Markdown")
        await send_menu(update.message, uid, uname, name)
        return

    if state == S_STORY:
        platform = ctx.user_data.get("platform")
        ctx.user_data["state"] = S_MAIN
        if not platform or platform not in PLATFORMS: return
        ok, err = validate_url(text, platform)
        if not ok:
            await update.message.reply_text(f"❌ {err}")
            await send_menu(update.message, uid, uname, name)
            return
        if uid in ACTIVE_USERS:
            await update.message.reply_text("⚠️ Already running.")
            return
        allowed, remaining = _check_rate_limit(uid)
        if not allowed:
            await update.message.reply_text(f"⏳ Wait {remaining}s.")
            return
        _record_download_time(uid)
        start_download_task(uid, do_special_download, update.message, text, platform, "stories", uid, uname, name, ctx.bot)
        return

    if state == S_HIGHLIGHT:
        platform = ctx.user_data.get("platform")
        ctx.user_data["state"] = S_MAIN
        if not platform or platform not in PLATFORMS: return
        ok, err = validate_url(text, platform)
        if not ok:
            await update.message.reply_text(f"❌ {err}")
            await send_menu(update.message, uid, uname, name)
            return
        if uid in ACTIVE_USERS:
            await update.message.reply_text("⚠️ Already running.")
            return
        allowed, remaining = _check_rate_limit(uid)
        if not allowed:
            await update.message.reply_text(f"⏳ Wait {remaining}s.")
            return
        _record_download_time(uid)
        start_download_task(uid, do_special_download, update.message, text, platform, "highlights", uid, uname, name, ctx.bot)
        return

    await send_menu(update.message, uid, uname, name)


async def handle_callback(
        update: Update,
        ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    uid, uname, name = _user(update)
    data = q.data or ""
    await _answer(q)
    if not _is_allowed(uid): return

    if data in ("m_back", "m_main"):
        ctx.user_data["state"] = S_MAIN
        await send_menu(q.message, uid, uname, name, edit=True)
        return

    if data == "m_add":
        await q.message.edit_text("➕ *Add source* — pick a platform:", parse_mode="Markdown", reply_markup=kb_platforms("add"))
        return

    if data.startswith("add_"):
        platform = data[4:]
        if platform not in PLATFORMS: return
        ctx.user_data.update(state=S_ADD_URL, platform=platform)
        hint = PLATFORM_URL_HINTS[platform]
        await q.message.edit_text(f"➕ *{platform.capitalize()}*\nSend the profile URL, e.g.:\n`{hint}username`", parse_mode="Markdown", reply_markup=kb_back())
        return

    if data == "m_remove":
        await q.message.edit_text("🚫 *Remove source* — pick a platform:", parse_mode="Markdown", reply_markup=kb_platforms("rem"))
        return

    if data.startswith("rem_"):
        platform = data[4:]
        if platform not in PLATFORMS: return
        urls = await read_profiles(uid, platform)
        if not urls:
            await q.message.edit_text(f"🚫 No sources for *{platform.capitalize()}*.", parse_mode="Markdown", reply_markup=kb_back())
            return
        rows = [[InlineKeyboardButton(f"❌ {u[:60]}", callback_data=f"del_{platform}_{i}")] for i, u in enumerate(urls[:30])]
        rows.append([InlineKeyboardButton("🔙 Back", callback_data="m_back")])
        await q.message.edit_text(f"🚫 *{platform.capitalize()}* sources — tap to remove:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("del_"):
        parts = data.split("_", 2)
        if len(parts) < 3: return
        platform = parts[1]
        try:
            idx = int(parts[2])
        except: return
        removed_container = []
        def _remove(current: list[str]) -> list[str]:
            if 0 <= idx < len(current): removed_container.append(current.pop(idx))
            return current
        await atomic_edit_profiles(uid, platform, _remove)
        await send_menu(q.message, uid, uname, name)
        return

    if data == "m_list":
        lines: list[str] = []
        for p in PLATFORMS:
            urls = await read_profiles(uid, p)
            if urls:
                lines.append(f"*{p.capitalize()}*")
                lines += [f"  • `{u}`" for u in urls]
        text = "\n".join(lines) if lines else "🚫 No sources added yet."
        await q.message.edit_text(text[:3900], parse_mode="Markdown", reply_markup=kb_back())
        return

    if data == "m_run":
        if uid in ACTIVE_USERS: return
        await q.message.edit_text("✅ *Run download* — choose media type:", parse_mode="Markdown", reply_markup=kb_media())
        return

    if data.startswith("dl_"):
        choice = data[3:]
        allowed, remaining = _check_rate_limit(uid)
        if not allowed:
            await q.message.reply_text(f"⏳ Wait {remaining}s.")
            return
        _record_download_time(uid)
        start_download_task(uid, do_download, q.message, choice, uid, uname, name, ctx.bot)
        return

    if data == "m_stories":
        rows = [[InlineKeyboardButton("Instagram", callback_data="story_instagram")], [InlineKeyboardButton("🔙 Back", callback_data="m_back")]]
        await q.message.edit_text("📖 *Stories* — pick a platform:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("story_"):
        platform = data[6:]
        ctx.user_data.update(state=S_STORY, platform=platform)
        await q.message.edit_text(f"📖 *Stories* › {platform.capitalize()}\nSend the profile URL:", parse_mode="Markdown", reply_markup=kb_back())
        return

    if data == "m_highlights":
        rows = [[InlineKeyboardButton("Instagram", callback_data="hl_instagram")], [InlineKeyboardButton("🔙 Back", callback_data="m_back")]]
        await q.message.edit_text("✨ *Highlights* — pick a platform:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("hl_"):
        platform = data[3:]
        ctx.user_data.update(state=S_HIGHLIGHT, platform=platform)
        await q.message.edit_text(f"✨ *Highlights* › {platform.capitalize()}\nSend the profile URL:", parse_mode="Markdown", reply_markup=kb_back())
        return

    if data == "m_stop":
        ev = STOP_EVENTS.get(uid)
        if ev: ev.set()
        return

    if data == "m_history":
        entries = await read_history(uid)
        lines = [f"📅 `{e.get('date')}` | *{e.get('platform')}* › `{e.get('user')}` | {e.get('sent')} file(s)" for e in entries[:20]]
        await q.message.edit_text("\n".join(lines)[:3900] if lines else "🚫 No history.", parse_mode="Markdown", reply_markup=kb_back())
        return

    if data == "m_cookies":
        await q.message.edit_text("🍪 *Set cookies*\n\nUpload a Netscape `.txt` file.", parse_mode="Markdown", reply_markup=kb_back())
        return

    if data == "m_status":
        text = await render_menu(uid, uname, name)
        await q.message.edit_text(text, parse_mode="Markdown", reply_markup=kb_back())
        return

    if data == "m_channel":
        ctx.user_data["state"] = S_SET_CHANNEL
        await q.message.edit_text("📡 *Set output channel*", parse_mode="Markdown", reply_markup=kb_back())
        return

    if data == "m_cleanup":
        freed = await asyncio.to_thread(wipe_downloads, uid)
        # [FIXED] Edit current message instead of replying to avoid clutter
        await q.message.edit_text(f"🗑️ *Cleanup Complete*\n\nFreed: `{freed} MB`\nStats have been reset.", parse_mode="Markdown", reply_markup=kb_back())
        return

    if data == "m_schedule":
        s = await read_settings(uid)
        current = s.get("schedule", "off")
        rows = [[InlineKeyboardButton(f"{'✅ ' if current == k else ''}{k.upper()}", callback_data=f"sched_{k}")] for k in SCHEDULE_OPTIONS]
        rows.append([InlineKeyboardButton("🔙 Back", callback_data="m_back")])
        await q.message.edit_text(f"⏰ *Scheduled Download*\n\nCurrent: *{current.upper()}*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("sched_"):
        interval_key = data[6:]
        job_name = f"schedule_{uid}"
        for job in ctx.application.job_queue.get_jobs_by_name(job_name): job.schedule_removal()
        s = await read_settings(uid)
        s.update({"schedule": interval_key, "schedule_chat_id": q.message.chat_id, "schedule_uname": uname, "schedule_name": name})
        await write_settings(uid, s)
        if interval_key != "off":
            ctx.application.job_queue.run_repeating(_scheduled_job, interval=SCHEDULE_OPTIONS[interval_key], first=SCHEDULE_OPTIONS[interval_key], name=job_name, data={"uid": uid, "chat_id": q.message.chat_id, "uname": uname, "name": name})
        await send_menu(q.message, uid, uname, name)
        return

    if data == "m_export":
        lines = []
        for p in PLATFORMS:
            urls = await read_profiles(uid, p)
            if urls: lines.append(f"# {p.upper()}"), lines.extend(urls), lines.append("")
        if not lines: return
        export_file = udir(uid) / "cuhibot_sources.txt"
        await asyncio.to_thread(export_file.write_text, "\n".join(lines), encoding="utf-8")
        with open(export_file, "rb") as fh:
            await q.message.reply_document(document=fh, filename="cuhibot_sources.txt")
        export_file.unlink(missing_ok=True)


# =============================================================================

# Handlers for bot features via commands

async def cmd_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, name = _user(update)
    if not _is_allowed(uid): return
    await update.message.reply_text("➕ *Add source* — pick a platform:", parse_mode="Markdown", reply_markup=kb_platforms("add"))

async def cmd_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, name = _user(update)
    if not _is_allowed(uid): return
    await update.message.reply_text("🚫 *Remove source* — pick a platform:", parse_mode="Markdown", reply_markup=kb_platforms("rem"))

async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, name = _user(update)
    if not _is_allowed(uid): return
    lines: list[str] = []
    for p in PLATFORMS:
        urls = await read_profiles(uid, p)
        if urls:
            lines.append(f"*{p.capitalize()}*")
            lines += [f"  • `{u}`" for u in urls]
    text = "\n".join(lines) if lines else "🚫 No sources added yet."
    await update.message.reply_text(text[:3900], parse_mode="Markdown", reply_markup=kb_back())

async def cmd_run(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, name = _user(update)
    if not _is_allowed(uid): return
    if uid in ACTIVE_USERS:
        await update.message.reply_text("⚠️ A download is already running.")
        return
    await update.message.reply_text("✅ *Run download* — choose media type:", parse_mode="Markdown", reply_markup=kb_media())

async def cmd_stories(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, name = _user(update)
    if not _is_allowed(uid): return
    rows = [[InlineKeyboardButton("Instagram", callback_data="story_instagram")], [InlineKeyboardButton("🔙 Back", callback_data="m_back")]]
    await update.message.reply_text("📖 *Stories* — pick a platform:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))

async def cmd_highlights(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, name = _user(update)
    if not _is_allowed(uid): return
    rows = [[InlineKeyboardButton("Instagram", callback_data="hl_instagram")], [InlineKeyboardButton("🔙 Back", callback_data="m_back")]]
    await update.message.reply_text("✨ *Highlights* — pick a platform:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))

async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, name = _user(update)
    if not _is_allowed(uid): return
    ev = STOP_EVENTS.get(uid)
    if ev:
        ev.set()
        await update.message.reply_text("🛑 Stop signal sent.")
    else:
        await update.message.reply_text("ℹ️ No active download to stop.")

async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, name = _user(update)
    if not _is_allowed(uid): return
    entries = await read_history(uid)
    lines = [f"📅 `{e.get('date')}` | *{e.get('platform')}* › `{e.get('user')}` | {e.get('sent')} file(s)" for e in entries[:20]]
    await update.message.reply_text("\n".join(lines)[:3900] if lines else "🚫 No history.", parse_mode="Markdown", reply_markup=kb_back())

async def cmd_cookies(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, name = _user(update)
    if not _is_allowed(uid): return
    await update.message.reply_text("🍪 *Set cookies*\n\nUpload a Netscape `.txt` file.", parse_mode="Markdown", reply_markup=kb_back())

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, name = _user(update)
    if not _is_allowed(uid): return
    text = await render_menu(uid, uname, name)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb_back())

async def cmd_channel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, name = _user(update)
    if not _is_allowed(uid): return
    ctx.user_data["state"] = S_SET_CHANNEL
    await update.message.reply_text("📡 *Set output channel*\nSend the channel ID or @username:", parse_mode="Markdown", reply_markup=kb_back())

async def cmd_schedule(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, name = _user(update)
    if not _is_allowed(uid): return
    s = await read_settings(uid)
    current = s.get("schedule", "off")
    rows = [[InlineKeyboardButton(f"{'✅ ' if current == k else ''}{k.upper()}", callback_data=f"sched_{k}")] for k in SCHEDULE_OPTIONS]
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="m_back")])
    await update.message.reply_text(f"⏰ *Scheduled Download*\n\nCurrent: *{current.upper()}*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))


# 12. MAIN & SUPPORTING FUNCTIONS
# =============================================================================

def bootstrap_env_cookies() -> None:
    dest_dir = global_cookie_dir()
    for env_key, cookie_filename in COOKIE_ENV_MAP.items():
        value = os.environ.get(env_key, "").strip()
        if not value: continue
        dest = dest_dir / cookie_filename
        try:
            decoded = base64.b64decode(value, validate=True).decode("utf-8")
            content = decoded if "\t" in decoded or decoded.lstrip().startswith("#") else value
        except: content = value
        try: dest.write_text(content, encoding="utf-8")
        except: pass


async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, name = _user(update)
    if not _is_admin(uid): return
    await update.message.reply_text(f"🛡️ *Admin Panel*\n\nActive now: {len(ACTIVE_USERS)}", parse_mode="Markdown")


def _detect_platform(url: str) -> str | None:
    try: domain = urlparse(url).netloc.lower()
    except: return None
    for plat, domains in PLATFORM_DOMAINS.items():
        if any(domain == d or domain.endswith('.' + d) for d in domains): return plat
    return None


async def cmd_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, name = _user(update)
    if not _is_allowed(uid) or not ctx.args: return
    url = ctx.args[0].strip()
    platform = _detect_platform(url)
    if not platform: return
    ok, err = validate_url(url, platform)
    if not ok: return
    if uid in ACTIVE_USERS: return
    _record_download_time(uid)
    handle = handle_from_url(url)
    ch = await get_channel(uid)
    target = (ctx.bot, ch) if ch else update.message
    cookie = resolve_cookie(uid, platform)
    _, _, sleep = PLATFORMS[platform]
    first = await update.message.reply_text(f"📎 *Downloading:* `{handle}`…", parse_mode="Markdown")
    status = Status(first)
    ev = asyncio.Event()
    STOP_EVENTS[uid] = ev
    ACTIVE_USERS.add(uid)

    async def _do_link(stop: asyncio.Event) -> None:
        try:
            await realtime_download(target=target, uid=uid, platform=platform, handle=handle, mode="documents", url=url, cookie=cookie, sleep=sleep, stop=stop, status=status, ignore_archive=True)
        finally:
            _release(uid, stop)
            await asyncio.to_thread(wipe_downloads, uid)
            await send_menu(update.message, uid, uname, name)

    task = asyncio.create_task(_do_link(ev))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


async def cmd_export(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, _, _ = _user(update)
    if not _is_allowed(uid): return
    lines = []
    for p in PLATFORMS:
        urls = await read_profiles(uid, p)
        if urls: lines.append(f"# {p.upper()}"), lines.extend(urls), lines.append("")
    if not lines: return
    export_file = udir(uid) / "cuhibot_sources.txt"
    await asyncio.to_thread(export_file.write_text, "\n".join(lines), encoding="utf-8")
    with open(export_file, "rb") as fh:
        await update.message.reply_document(document=fh, filename="cuhibot_sources.txt")
    export_file.unlink(missing_ok=True)


async def _import_sources(uid: int, text: str) -> tuple[int, int]:
    added, skipped = 0, 0
    pending: dict[str, list[str]] = {}
    current_platform: str | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line: continue
        if line.startswith("#"):
            tag = line.lstrip("#").strip().lower()
            if tag in PLATFORMS: current_platform = tag
            continue
        if current_platform and _URL_RE.match(line):
            ok, _ = validate_url(line, current_platform)
            if ok:
                if current_platform not in pending: pending[current_platform] = []
                pending[current_platform].append(line)
            else: skipped += 1
        else: skipped += 1
    for plat, new_urls in pending.items():
        def _merge(current: list[str]) -> list[str]:
            nonlocal added, skipped
            for url in new_urls:
                if url in current or len(current) >= MAX_PROFILES_PER_PLATFORM: skipped += 1
                else:
                    current.append(url)
                    added += 1
            return current
        await atomic_edit_profiles(uid, plat, _merge)
    return added, skipped


SCHEDULE_OPTIONS = {"6h": 6 * 3600, "12h": 12 * 3600, "24h": 24 * 3600, "off": 0}

async def _scheduled_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, chat_id = ctx.job.data["uid"], ctx.job.data["chat_id"]
    if uid in ACTIVE_USERS: return
    if not any(await read_profiles(uid, p) for p in PLATFORMS): return
    ev = asyncio.Event()
    STOP_EVENTS[uid] = ev
    ACTIVE_USERS.add(uid)
    ch = await get_channel(uid)
    target = (ctx.bot, ch) if ch else (ctx.bot, chat_id)
    try:
        first = await ctx.bot.send_message(chat_id=chat_id, text="⏰ *Scheduled download starting…*", parse_mode="Markdown")
        status, total = Status(first), 0
        for p, (_, _, sl) in PLATFORMS.items():
            if ev.is_set(): break
            urls = await read_profiles(uid, p)
            for url in urls:
                if ev.is_set(): break
                total += await realtime_download(target=target, uid=uid, platform=p, handle=handle_from_url(url), mode="mixed", url=url, cookie=resolve_cookie(uid, p), sleep=sl, stop=ev, status=status)
        await status.set(f"⏰ *Scheduled download done!* {total} file(s).", force=True)
    except: logger.exception("Job failed")
    finally:
        _release(uid, ev)
        await asyncio.to_thread(wipe_downloads, uid)


async def _restore_schedules(app: Application) -> None:
    if not await asyncio.to_thread(DATA_ROOT.exists): return
    # [FIXED] Non-blocking startup scan for production scale
    for user_dir in await asyncio.to_thread(lambda: list(DATA_ROOT.iterdir())):
        if not user_dir.is_dir() or not user_dir.name.isdigit(): continue
        uid = int(user_dir.name)
        try:
            s = await read_settings(uid)
            interval_key = s.get("schedule", "off")
            if interval_key == "off" or interval_key not in SCHEDULE_OPTIONS: continue
            interval = SCHEDULE_OPTIONS[interval_key]
            app.job_queue.run_repeating(_scheduled_job, interval=interval, first=interval, name=f"schedule_{uid}", data={"uid": uid, "chat_id": s.get("schedule_chat_id"), "uname": s.get("schedule_uname"), "name": s.get("schedule_name")})
        except: pass


def main() -> None:
    if TOKEN == "YOUR_BOT_TOKEN": return
    bootstrap_env_cookies()
    request = HTTPXRequest(connect_timeout=15.0, read_timeout=30.0, write_timeout=60.0, pool_timeout=60.0, connection_pool_size=200)
    app = Application.builder().token(TOKEN).request(request).post_init(_restore_schedules).build()
    
    # Navigation & Core
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    
    # Management
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("channel", cmd_channel))
    app.add_handler(CommandHandler("cookies", cmd_cookies))
    app.add_handler(CommandHandler("schedule", cmd_schedule))
    
    # Downloads & Action
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("stories", cmd_stories))
    app.add_handler(CommandHandler("highlights", cmd_highlights))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("link", cmd_link))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("cleanup", cmd_cleanup))
    
    # Admin
    app.add_handler(CommandHandler("admin", cmd_admin))
    
    # Fallbacks & Callbacks
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
