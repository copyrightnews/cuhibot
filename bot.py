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
import json
import logging
import os
import re
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
    Update,
)
from telegram.error import BadRequest, RetryAfter, TimedOut
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
TELEGRAM_FILE_LIMIT = 50 * 1024 * 1024  # 50 MB — Telegram Bot API upload cap

# ── Security constants ───────────────────────────────────────────────────────
# Comma-separated Telegram user IDs. If empty/unset, ALL users are allowed.
# Example: ALLOWED_USERS=123456789,987654321
_ALLOWED_RAW = os.environ.get("ALLOWED_USERS", "").strip()
ALLOWED_USERS: set[int] = (
    {int(x.strip()) for x in _ALLOWED_RAW.split(",") if x.strip().isdigit()}
    if _ALLOWED_RAW else set()
)

# Comma-separated admin IDs. Admins can use /admin commands.
_ADMIN_RAW = os.environ.get("ADMIN_IDS", "").strip()
ADMIN_IDS: set[int] = (
    {int(x.strip()) for x in _ADMIN_RAW.split(",") if x.strip().isdigit()}
    if _ADMIN_RAW else set()
)

MAX_PROFILES_PER_PLATFORM = 50     # prevent abuse
MAX_URL_LENGTH            = 500    # sane limit
MAX_COOKIE_FILE_BYTES     = 1_048_576   # 1 MB
RATE_LIMIT_SECONDS        = 30     # min gap between download starts per user

# State keys
S_MAIN, S_ADD_URL, S_SET_CHANNEL, S_STORY, S_HIGHLIGHT = (
    "main", "add_url", "set_channel", "story_url", "highlight_url"
)

# Runtime registries
STOP_EVENTS: dict[int, asyncio.Event] = {}
ACTIVE_USERS: set[int]                = set()   # BUG-10
_LAST_DOWNLOAD: dict[int, float]      = {}       # rate-limit tracker
_TASKS: set[asyncio.Task]             = set()    # BUG-52: prevent GC of fire-and-forget tasks


# =============================================================================
# 2. LOCKING, DISK, PATH UTILITIES
# =============================================================================

@contextmanager
def locked_file(target: Path):
    """Atomic advisory file lock (cross-platform).

    Uses os.open with O_CREAT|O_EXCL for atomic lock creation.
    Retries up to 20 times (2 seconds total), then raises instead
    of silently proceeding without the lock.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_suffix(target.suffix + ".lock")
    fd = None
    max_retries = 20
    for attempt in range(max_retries):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break  # acquired
        except FileExistsError:
            # Remove stale locks (older than 30 seconds) and retry immediately
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > 30:
                    lock_path.unlink(missing_ok=True)
                    continue  # retry this attempt without sleeping
            except OSError:
                pass
            # Not stale — wait and try again unless we're on the last attempt
            if attempt == max_retries - 1:
                raise TimeoutError(
                    f"Could not acquire lock on {target} after {max_retries} retries"
                )
            time.sleep(0.001)   # BUG-A: minimal sleep; contention is near-impossible (per-user files)

    # Guard: if somehow fd is still None (e.g. stale-unlock exhausted retries)
    if fd is None:
        raise TimeoutError(
            f"Could not acquire lock on {target} (lock never obtained)"
        )
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
    """Persistent gallery-dl archive, stored OUTSIDE the volatile download dir. BUG-05, BUG-11."""
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
    return [line.strip() for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_profiles(uid: int, platform: str, urls: Iterable[str]) -> None:
    path = profiles_path(uid, platform)
    url_list = list(urls)
    with locked_file(path):
        if url_list:
            path.write_text("\n".join(url_list) + "\n", encoding="utf-8")
        else:
            # Truncate the file cleanly when all profiles are removed
            path.write_text("", encoding="utf-8")


def read_history(uid: int) -> list[dict]:
    p = history_path(uid)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def append_history(uid: int, entry: dict) -> None:
    path = history_path(uid)
    with locked_file(path):
        current = read_history(uid)
        current.insert(0, entry)
        path.write_text(json.dumps(current[:50], indent=2), encoding="utf-8")


def read_settings(uid: int) -> dict:
    p = settings_path(uid)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_settings(uid: int, data: dict) -> None:
    path = settings_path(uid)
    with locked_file(path):
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")


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
    """Return total sent files, seeding from history on first access.
    BUG-73: uses atomic read-modify-write under lock to avoid TOCTOU races.
    """
    s = read_settings(uid)
    if "total_sent_files" in s:
        return s["total_sent_files"]
    # First call: seed from history under lock so we don't overwrite concurrent changes
    path = settings_path(uid)
    with locked_file(path):
        try:
            s = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            s = {}
        if "total_sent_files" not in s:
            count = sum(e.get("sent", 0) for e in read_history(uid))
            s["total_sent_files"] = count
            path.write_text(json.dumps(s, indent=2), encoding="utf-8")
    return s.get("total_sent_files", 0)


def add_sent_files(uid: int, count: int) -> None:
    if count <= 0:
        return
    path = settings_path(uid)
    with locked_file(path):  # BUG-D: atomic read-modify-write under lock
        try:
            s = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            s = {}
        s["total_sent_files"] = s.get("total_sent_files", 0) + count
        path.write_text(json.dumps(s, indent=2), encoding="utf-8")


def total_downloaded_mb(uid: int) -> float:
    """Return cumulative downloaded bytes as MB from persistent settings."""
    return round(read_settings(uid).get("total_bytes", 0) / (1024 * 1024), 1)


def add_downloaded_bytes(uid: int, nbytes: int) -> None:
    """Increment cumulative downloaded-byte counter in settings."""
    if nbytes <= 0:
        return
    path = settings_path(uid)
    with locked_file(path):  # BUG-D: atomic read-modify-write under lock
        try:
            s = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            s = {}
        s["total_bytes"] = s.get("total_bytes", 0) + nbytes
        path.write_text(json.dumps(s, indent=2), encoding="utf-8")


# =============================================================================
# 4. VALIDATORS & NORMALIZERS
# =============================================================================

_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)


def validate_url(url: str, platform: str) -> tuple[bool, str]:
    """BUG-07: ensure input is both well-formed AND on the right domain."""
    if len(url) > MAX_URL_LENGTH:
        return False, f"URL too long (max {MAX_URL_LENGTH} characters)."
    if not _URL_RE.match(url):
        return False, "Not a valid URL (must start with http:// or https://)."
    # Block newlines (HTTP header injection) and shell metacharacters
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
    """BUG-03: convert user-entered channel strings into valid Telegram chat IDs.

    Returns @username strings as-is, or numeric IDs as int.
    Positive numbers get the -100 supergroup/channel prefix.
    Already-negative numbers are kept as-is.
    """
    if isinstance(value, int):
        return value
    v = str(value).strip()
    if v.startswith("@"):
        return v
    if v.lstrip("-").isdigit():
        n = int(v)
        if n < 0:
            return n
        # Always prepend -100 for positive numeric IDs
        return int(f"-100{n}")
    return v


def handle_from_url(url: str) -> str:
    """Extract the username/handle from a profile URL.

    Strips query strings and fragments before splitting so that
    e.g. https://instagram.com/user/?hl=en correctly returns 'user'
    instead of 'user?hl=en', which would corrupt archive directory names.
    """
    # Strip query string and fragment first
    clean = url.split("?")[0].split("#")[0]
    return clean.rstrip("/").split("/")[-1].lstrip("@")


def stories_url_for(platform: str, url: str) -> str:
    """BUG-01: explicit rewrite so intent is unambiguous."""
    if platform == "instagram":
        return f"https://www.instagram.com/stories/{handle_from_url(url)}/"
    return url


def highlights_url_for(platform: str, url: str) -> str:
    """BUG-14: use gallery-dl's real highlights endpoint.

    gallery-dl expects /{username}/highlights/ (not /stories/highlights/{username}/).
    """
    if platform == "instagram":
        return f"https://www.instagram.com/{handle_from_url(url)}/highlights/"
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
         InlineKeyboardButton("⏰ Schedule",       callback_data="m_schedule")],
        [InlineKeyboardButton("📎 Export sources", callback_data="m_export"),
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


def _escape_md(text: str) -> str:
    """Escape Markdown special characters in user-supplied strings."""
    for ch in ('*', '_', '`', '[', ']', '~'):
        text = text.replace(ch, f'\\{ch}')
    return text


def render_menu(uid: int, username: str, name: str) -> str:
    safe_username = _escape_md(username)
    safe_name = _escape_md(name)
    ch = get_channel(uid)
    ch_line   = f"\n📡 Output: *{ch}*" if ch else ""
    cached = total_downloaded_mb(uid)
    if cached >= 1024:
        disk_line = f"\n💾 Downloaded: *{round(cached / 1024, 2)} GB*"
    else:
        disk_line = f"\n💾 Downloaded: *{cached} MB*"
    return (
        f"@{safe_username}, {safe_name}\n"
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
        "👨‍💻 Developer: @copyrightnews"
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
        logger.exception("send_menu failed for uid=%s", uid)


# =============================================================================
# 6. STATUS THROTTLER
# =============================================================================

@dataclass
class Status:
    """BUG-06: rate-limited edit_text wrapper that respects RetryAfter."""
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
            # Don't sleep and block the orchestrator; just defer future updates
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
        cmd += ["--filter", "extension in ('jpg','jpeg','png','gif','webp','bmp','mp4','webm','mkv','mov','avi','m4v')"]
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
    """Send a media group once.

    No retry here — file handles inside InputMediaPhoto/Video objects are
    at EOF after the first attempt.  flush() catches any exception and
    falls back to _send_one() per file, which reopens each file cleanly
    and has its own retry logic.
    """
    if hasattr(target, "reply_media_group"):
        await target.reply_media_group(group)
    else:
        bot, cid = target
        await bot.send_media_group(chat_id=cid, media=group)


async def _send_one(target, f: Path, kind: str, *, _retries: int = 0) -> bool:
    """Send a single file. Returns True on success, False on failure.

    Retries up to 4 times on TimedOut (large file upload) and RetryAfter
    (rate limit) with exponential backoff.
    """
    # Skip files that exceed Telegram's 50 MB upload limit
    try:
        fsize = f.stat().st_size
    except OSError:
        fsize = 0
    if fsize > TELEGRAM_FILE_LIMIT:
        logger.warning("Skipping %s (%.1f MB) — exceeds Telegram 50 MB limit",
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
            logger.warning("Giving up on %s after %d RetryAfter retries", f.name, _retries)
            return False
        wait = exc.retry_after + 1.0
        logger.info("RetryAfter on %s — waiting %.1fs (attempt %d)", f.name, wait, _retries + 1)
        await asyncio.sleep(wait)
        return await _send_one(target, f, kind, _retries=_retries + 1)
    except TimedOut:
        if _retries >= 4:
            logger.warning("Giving up on %s after %d TimedOut retries", f.name, _retries)
            return False
        wait = 5.0 * (2 ** _retries)   # 5s, 10s, 20s, 40s
        logger.info("TimedOut on %s — waiting %.1fs then retrying (attempt %d)",
                    f.name, wait, _retries + 1)
        await asyncio.sleep(wait)
        return await _send_one(target, f, kind, _retries=_retries + 1)
    except Exception:
        logger.warning("Failed to send %s as %s", f.name, kind, exc_info=True)
        return False


def _chunk_batch(batch: list[Path]) -> list[list[Path]]:
    """Split a batch into Telegram-compatible chunks of at most 10 files each."""
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


async def flush(target, batch: list[Path], send_as: str) -> int:
    """Send the buffered batch, delete successfully-sent files.

    Returns the number of files that were actually sent.
    Files that fail to send are kept on disk so they can be retried
    (the download archive won't re-download them, but the file remains).
    """
    if not batch:
        return 0

    sent = 0
    sent_files: list[Path] = []  # track which files were actually delivered

    try:
        if send_as == "documents":
            for f in batch:
                if await _send_one(target, f, "document"):
                    sent += 1
                    sent_files.append(f)

        elif len(batch) == 1:
            f = batch[0]
            kind = {"photos": "photo", "videos": "video"}.get(send_as) or file_kind(f)
            if await _send_one(target, f, kind):
                sent += 1
                sent_files.append(f)

        else:
            for chunk in _chunk_batch(batch):
                for _retries in range(5):
                    file_handles: list = []
                    try:
                        if send_as == "photos":
                            for f in chunk:
                                file_handles.append(open(f, "rb"))
                            group = [InputMediaPhoto(fh) for fh in file_handles]
                        elif send_as == "videos":
                            for f in chunk:
                                file_handles.append(open(f, "rb"))
                            group = [InputMediaVideo(fh) for fh in file_handles]
                        else:  # mixed
                            group = []
                            for f in chunk:
                                fh = open(f, "rb")
                                file_handles.append(fh)
                                if file_kind(f) == "photo":
                                    group.append(InputMediaPhoto(fh))
                                else:
                                    group.append(InputMediaVideo(fh))
                        await _send_group(target, group)
                        sent += len(chunk)
                        sent_files.extend(chunk)
                        for fh in file_handles:
                            try: fh.close()
                            except Exception: pass
                        break  # Success
                    except RetryAfter as exc:
                        for fh in file_handles:
                            try: fh.close()
                            except Exception: pass
                        if _retries >= 3:
                            logger.warning("Giving up group send after %d RetryAfter retries", _retries)
                            for f in chunk:
                                kind = {"photos": "photo", "videos": "video"}.get(send_as) or file_kind(f)
                                if await _send_one(target, f, kind):
                                    sent += 1
                                    sent_files.append(f)
                            break
                        wait = exc.retry_after + 1.0
                        logger.info("RetryAfter on group send — waiting %.1fs", wait)
                        await asyncio.sleep(wait)
                    except TimedOut:
                        for fh in file_handles:
                            try: fh.close()
                            except Exception: pass
                        if _retries >= 4:
                            logger.warning("Giving up group send after %d TimedOut retries", _retries)
                            for f in chunk:
                                kind = {"photos": "photo", "videos": "video"}.get(send_as) or file_kind(f)
                                if await _send_one(target, f, kind):
                                    sent += 1
                                    sent_files.append(f)
                            break
                        wait = 5.0 * (2 ** _retries)
                        logger.info("TimedOut on group send — waiting %.1fs then retrying", wait)
                        await asyncio.sleep(wait)
                    except Exception as e:
                        for fh in file_handles:
                            try: fh.close()
                            except Exception: pass
                        logger.warning("Group send failed: %s, falling back to individual sends", str(e))
                        for f in chunk:
                            kind = {"photos": "photo", "videos": "video"}.get(send_as) or file_kind(f)
                            if await _send_one(target, f, kind):
                                sent += 1
                                sent_files.append(f)
                        break
    finally:
        # Only delete files that were actually sent successfully
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
        stderr=asyncio.subprocess.PIPE,   # capture so we can log errors
    )

    # BUG-B: drain stderr continuously to prevent deadlock if gallery-dl writes >64KB of errors
    stderr_buf = bytearray()
    async def _drain_stderr():
        if not proc.stderr: return
        try:
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    break
                stderr_buf.extend(chunk)
                if len(stderr_buf) > 1024 * 1024:
                    del stderr_buf[:-512*1024]
        except Exception:
            pass
    
    stderr_task = asyncio.create_task(_drain_stderr())

    seen: set[Path] = set()
    buffer: list[Path] = []
    sent_count = 0
    downloaded_bytes = 0  # track cumulative file sizes for this run

    async def drain() -> None:
        nonlocal sent_count
        if not buffer:
            return
        batch = list(buffer)
        # flush() returns the count of files actually sent successfully
        n = await flush(target, batch, send_as)
        sent_count += n
        buffer.clear()  # only clear AFTER flush returns

    try:
        # BUG-46 fix: non-blocking returncode check first; sleep only when idle.
        while True:
            if stop.is_set():
                try:
                    proc.kill()
                except Exception:
                    pass
                break

            # Non-blocking exit check — does NOT block the event loop
            if proc.returncode is not None:
                break  # post-loop final sweep handles any remaining files

            if out_dir.exists():
                new_files = []
                for f in sorted(out_dir.iterdir()):
                    if f in seen or not f.is_file():
                        continue
                    if f.suffix.lower() not in exts:
                        continue
                    if f.name.endswith(('.part', '.ytdl', '.tmp')):
                        continue
                    new_files.append(f)

                if new_files:
                    sizes1 = {}
                    for f in new_files:
                        try:
                            sizes1[f] = f.stat().st_size
                        except OSError:
                            pass

                    await asyncio.sleep(0.5)  # stability: one bulk sleep for all new files

                    for f in new_files:
                        try:
                            s2 = f.stat().st_size
                            s1 = sizes1.get(f)
                            if s1 is None or s1 != s2:
                                continue
                        except OSError:
                            continue

                        seen.add(f)
                        buffer.append(f)
                        try:
                            downloaded_bytes += f.stat().st_size
                        except OSError:
                            pass
                        if len(buffer) >= MEDIA_GROUP_MAX:
                            await drain()
                            if status:
                                await status.set(f"📦 `{handle}` › {sent_count} file(s) sent…")
                else:
                    await asyncio.sleep(0.5)  # nothing new — yield before next poll
            else:
                await asyncio.sleep(0.5)  # output dir not ready yet — yield


        # Final sweep after subprocess exited cleanly
        if not stop.is_set() and out_dir.exists():
            await asyncio.sleep(0.3)
            for f in sorted(out_dir.iterdir()):
                if f in seen or not f.is_file():
                    continue
                if f.suffix.lower() not in exts:
                    continue
                if f.name.endswith(('.part', '.ytdl', '.tmp')):
                    continue
                seen.add(f)
                buffer.append(f)
                try:
                    downloaded_bytes += f.stat().st_size
                except OSError:
                    pass
                if len(buffer) >= MEDIA_GROUP_MAX:
                    await drain()

        await drain()

        if status:
            await status.set(
                f"📦 `{handle}` → {sent_count} file(s) on `{mode}`.",
                force=True,
            )
    finally:
        # Clean up non-media temp files; keep unsent media for automatic
        # retry on the next run.  (shutil.rmtree would destroy files that
        # flush() deliberately kept because they failed to send.)
        if out_dir.exists():
            all_media_ext = PHOTO_EXT | VIDEO_EXT
            for f in list(out_dir.iterdir()):
                if f.is_file() and f.suffix.lower() not in all_media_ext:
                    try:
                        f.unlink(missing_ok=True)
                    except OSError:
                        pass
            # Remove dir only if empty (all media was sent or no files)
            try:
                out_dir.rmdir()
            except OSError:
                pass  # still has unsent media — leave it
        # Kill process if still running
        if proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except Exception:
                pass
        # BUG-B: cancel the drain task and log collected stderr
        stderr_task.cancel()
        try:
            if stderr_buf:
                logger.warning(
                    "gallery-dl stderr [%s/%s]: %s",
                    platform, handle,
                    stderr_buf.decode(errors="replace").strip(),
                )
        except Exception:
            pass

    # Persist cumulative downloaded bytes
    if downloaded_bytes > 0:
        add_downloaded_bytes(uid, downloaded_bytes)
        
    if sent_count > 0:
        add_sent_files(uid, sent_count)

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
    # ch is already normalized at save time — don't re-normalize
    target = (bot, ch) if ch else msg

    first = await msg.reply_text(
        f"⏳ *{label}* — starting…" + (f"\n📡 → {ch}" if ch else ""),
        parse_mode="Markdown",
    )
    status = Status(first)

    started = datetime.now()
    total   = 0

    try:
        for platform, (_, _, sleep) in PLATFORMS.items():
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
        if total == 0 and not stop.is_set():
            final = "ℹ️ *No new media found.* (all already downloaded or no sources set)"
        elif stop.is_set():
            final = f"⏹️ *Stopped.* {total} file(s) in {elapsed}s."
        else:
            final = f"✅ *Done!* {total} file(s) in {elapsed}s."
        await status.set(final, force=True)
    finally:
        _release(uid, stop)
        # Feature: auto-cleanup — free disk after every download run
        wipe_downloads(uid)
        await send_menu(msg, uid, uname, name)


async def do_special_download(msg, url: str, platform: str, mode: str,
                              uid: int, uname: str, name: str, bot,
                              stop: asyncio.Event) -> None:
    label = "📖 Stories" if mode == "stories" else "🌟 Highlights"
    ch    = get_channel(uid)
    # ch is already normalized at save time — don't re-normalize
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
            append_history(uid, {
                "date":     datetime.now().strftime("%Y-%m-%d %H:%M"),
                "platform": platform,
                "user":     handle,
                "media":    mode,
                "sent":     n,
            })
        if n == 0 and not stop.is_set():
            await status.set("ℹ️ *No new media found.* (already downloaded or private)", force=True)
        elif stop.is_set():
            await status.set(f"⏹️ *Stopped.* {n} file(s) sent.", force=True)
        else:
            await status.set(f"✅ *Done!* {n} file(s) sent.", force=True)
    finally:
        _release(uid, stop)
        # Feature: auto-cleanup — free disk after every download run
        wipe_downloads(uid)
        await send_menu(msg, uid, uname, name)


def start_download_task(uid: int, coro_func, *args) -> None:
    """
    Register a fresh stop-event for `uid`, then fire the coroutine as a task.
    BUG-09/10: signals any pre-existing run to quit before starting a new one.
    BUG-52: stores task reference in _TASKS to prevent Python GC.
    """
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

# ── helpers ──────────────────────────────────────────────────────────────────

def _user(update: Update) -> tuple[int, str, str]:
    u = update.effective_user
    return u.id, u.username or "unknown", u.first_name or "User"


def _is_allowed(uid: int) -> bool:
    """Check if a user is allowed to use the bot.
    If ALLOWED_USERS is empty, everyone is allowed (open mode).
    Admins are always allowed.
    """
    if not ALLOWED_USERS:
        return True   # open mode
    return uid in ALLOWED_USERS or uid in ADMIN_IDS


def _is_admin(uid: int) -> bool:
    """Check if a user is a bot admin."""
    return uid in ADMIN_IDS


def _check_rate_limit(uid: int) -> tuple[bool, int]:
    """Returns (allowed, seconds_remaining)."""
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


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, name = _user(update)
    if not _is_allowed(uid):
        await update.message.reply_text(
            "🔒 *Access Denied*\n\nYou are not authorized to use this bot.",
            parse_mode="Markdown",
        )
        logger.warning("Unauthorized access attempt by uid=%s username=%s", uid, uname)
        return
    await send_menu(update.message, uid, uname, name)


# ── /cleanup  (also reachable via button) ────────────────────────────────────

async def cmd_cleanup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, name = _user(update)
    if not _is_allowed(uid):
        await update.message.reply_text("🔒 Access denied.")
        return
    if uid in ACTIVE_USERS:
        await update.message.reply_text("⚠️ Please stop the active download before freeing disk space.")
        return
    freed = wipe_downloads(uid)
    await update.message.reply_text(
        f"🗑️ Freed *{freed} MB* of cached downloads.", parse_mode="Markdown"
    )
    await send_menu(update.message, uid, uname, name)


# ── cookie upload handler ─────────────────────────────────────────────────────

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Accept a .txt cookies file and save it to the user's cookie dir."""
    uid, uname, name = _user(update)
    if not _is_allowed(uid):
        await update.message.reply_text("🔒 Access denied.")
        return
    doc = update.message.document
    if not doc or not doc.file_name:
        return

    # Security: enforce file size limit
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
        # Check if it's a source import file
        if fname in ("cuhibot_sources.txt", "sources.txt"):
            try:
                tg_file = await doc.get_file()
                raw = await tg_file.download_as_bytearray()
                text_content = raw.decode("utf-8", errors="replace")
                added, skipped = _import_sources(uid, text_content)
                await update.message.reply_text(
                    f"📋 *Import complete!*\n"
                    f"• Added: {added} source(s)\n"
                    f"• Skipped: {skipped} (duplicates, invalid, or limit reached)",
                    parse_mode="Markdown",
                )
                await send_menu(update.message, uid, uname, name)
            except Exception:
                logger.exception("Source import failed for uid=%s", uid)
                await update.message.reply_text("❌ Failed to import sources.")
            return
        await update.message.reply_text(
            "⚠️ Unrecognised cookie file name.\n"
            "Expected one of:\n" +
            "\n".join(f"  • `{v[1]}`" for v in PLATFORMS.values()),
            parse_mode="Markdown",
        )
        return

    platform, cookie_name = matched
    dest = cdir(uid) / cookie_name
    try:
        tg_file = await doc.get_file()
        await tg_file.download_to_drive(str(dest))
    except Exception:
        logger.exception("Cookie download failed for uid=%s platform=%s", uid, platform)
        await update.message.reply_text(
            "❌ Failed to save the cookie file. Please try again."
        )
        return
    # BUG-54: verify file size on disk (Telegram metadata may be missing)
    try:
        actual_size = dest.stat().st_size
        if actual_size > MAX_COOKIE_FILE_BYTES:
            dest.unlink(missing_ok=True)
            await update.message.reply_text(
                f"⚠️ Cookie file too large ({actual_size // 1024} KB, max {MAX_COOKIE_FILE_BYTES // 1024} KB)."
            )
            return
    except OSError:
        pass
    await update.message.reply_text(
        f"🍪 Cookies saved for *{platform.capitalize()}*.", parse_mode="Markdown"
    )
    await send_menu(update.message, uid, uname, name)


# ── text / URL handler ────────────────────────────────────────────────────────

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid, uname, name = _user(update)
    if not _is_allowed(uid):
        await update.message.reply_text("🔒 Access denied.")
        return
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
        elif len(existing) >= MAX_PROFILES_PER_PLATFORM:
            await update.message.reply_text(
                f"⚠️ Maximum {MAX_PROFILES_PER_PLATFORM} sources per platform reached."
            )
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
        if uid in ACTIVE_USERS:
            await update.message.reply_text("⚠️ A download is already running. Tap 🚫 Stop first.")
            await send_menu(update.message, uid, uname, name)
            return
        allowed, remaining = _check_rate_limit(uid)
        if not allowed:
            await update.message.reply_text(f"⏳ Please wait {remaining}s before starting another download.")
            await send_menu(update.message, uid, uname, name)  # BUG-66: re-render menu
            return
        _record_download_time(uid)
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
        if uid in ACTIVE_USERS:
            await update.message.reply_text("⚠️ A download is already running. Tap 🚫 Stop first.")
            await send_menu(update.message, uid, uname, name)
            return
        allowed, remaining = _check_rate_limit(uid)
        if not allowed:
            await update.message.reply_text(f"⏳ Please wait {remaining}s before starting another download.")
            await send_menu(update.message, uid, uname, name)  # BUG-66: re-render menu
            return
        _record_download_time(uid)
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

    # Security: access control
    if not _is_allowed(uid):
        await q.message.reply_text("🔒 Access denied.")
        return

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
        parts = data.split("_", 2)
        if len(parts) < 3:
            await send_menu(q.message, uid, uname, name)
            return
        platform = parts[1]
        if platform not in PLATFORMS:
            await send_menu(q.message, uid, uname, name)
            return
        try:
            idx = int(parts[2])
        except (ValueError, IndexError):
            await send_menu(q.message, uid, uname, name)
            return
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
        # BUG-77: Telegram edit_text limit is 4096 chars; truncate to avoid BadRequest
        _MAX = 3900
        text = "\n".join(lines) if lines else "ℹ️ No sources added yet."
        if len(text) > _MAX:
            text = text[:_MAX] + "\n…_(truncated — use 📎 Export to see all)_"
        await q.message.edit_text(text, parse_mode="Markdown", reply_markup=kb_back())
        return

    # ── run download ─────────────────────────────────────────────────────────
    if data == "m_run":
        if uid in ACTIVE_USERS:
            await q.message.reply_text(
                "⚠️ A download is already running. Tap 🚫 Stop first."
            )
            return
        if not any(read_profiles(uid, p) for p in PLATFORMS):
            await q.message.reply_text(
                "ℹ️ No sources added yet. Tap ➕ Add source first."
            )
            return
        await q.message.edit_text(
            "✅ *Run download* — choose media type:",
            parse_mode="Markdown", reply_markup=kb_media(),
        )
        return

    if data.startswith("dl_"):
        choice = data[3:]
        if choice not in ("1", "2", "3", "4"):
            return
        if uid in ACTIVE_USERS:
            await q.message.reply_text("⚠️ Already running.")
            return
        if not any(read_profiles(uid, p) for p in PLATFORMS):
            await q.message.reply_text(
                "ℹ️ No sources added yet. Tap ➕ Add source first."
            )
            return
        # Rate limiting
        allowed, remaining = _check_rate_limit(uid)
        if not allowed:
            await q.message.reply_text(
                f"⏳ Please wait {remaining}s before starting another download."
            )
            return
        _record_download_time(uid)
        start_download_task(
            uid,
            do_download,
            q.message, choice, uid, uname, name, ctx.bot,
        )
        return

    # ── stories ──────────────────────────────────────────────────────────────
    if data == "m_stories":
        # BUG-53: only Instagram supports stories via gallery-dl
        rows = [[InlineKeyboardButton("Instagram", callback_data="story_instagram")]]
        rows.append([InlineKeyboardButton("🔙 Back", callback_data="m_back")])
        await q.message.edit_text(
            "📖 *Stories* — pick a platform:\n"
            "_(Currently only Instagram is supported)_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows),
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
        # BUG-53: only Instagram supports highlights via gallery-dl
        rows = [[InlineKeyboardButton("Instagram", callback_data="hl_instagram")]]
        rows.append([InlineKeyboardButton("🔙 Back", callback_data="m_back")])
        await q.message.edit_text(
            "✨ *Highlights* — pick a platform:\n"
            "_(Currently only Instagram is supported)_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows),
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
        # BUG-76: Telegram edit_text limit is 4096 chars; truncate to avoid BadRequest
        _MAX = 3900
        full_text = "\n".join(lines)
        if len(full_text) > _MAX:
            full_text = full_text[:_MAX] + "\n…_(truncated)_"
        await q.message.edit_text(
            full_text, parse_mode="Markdown", reply_markup=kb_back()
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
        dl_mb  = total_downloaded_mb(uid)
        dl_str = f"{round(dl_mb / 1024, 2)} GB" if dl_mb >= 1024 else f"{dl_mb} MB"
        active = "▶️ Running" if uid in ACTIVE_USERS else "⏸️ Idle"
        ch     = get_channel(uid) or "Direct chat"
        text   = (
            f"📊 *Status*\n\n"
            f"• State      : {active}\n"
            f"• Sources    : {total_profiles(uid)}\n"
            f"• Cookies    : {cookie_summary(uid)}\n"
            f"• Channel    : `{ch}`\n"
            f"• Downloaded : {dl_str}\n"
            f"• Sent       : {total_sent(uid)} file(s)"
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
        if uid in ACTIVE_USERS:
            await q.message.reply_text("⚠️ Please stop the active download before freeing disk space.")
            return
        freed = wipe_downloads(uid)
        await q.message.reply_text(
            f"🗑️ Freed *{freed} MB* of cached downloads.", parse_mode="Markdown"
        )
        await send_menu(q.message, uid, uname, name)
        return

    # ── schedule ──────────────────────────────────────────────────────────────
    if data == "m_schedule":
        s = read_settings(uid)
        current = s.get("schedule", "off")
        rows = [
            [InlineKeyboardButton(
                f"{'✅ ' if current == k else ''}{k.upper()}",
                callback_data=f"sched_{k}",
            )]
            for k in SCHEDULE_OPTIONS
        ]
        rows.append([InlineKeyboardButton("🔙 Back", callback_data="m_back")])
        await q.message.edit_text(
            f"⏰ *Scheduled Auto-Download*\n\n"
            f"Current: *{current.upper()}*\n\n"
            "Pick an interval — the bot will automatically download\n"
            "new media from all your sources on a timer.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if data.startswith("sched_"):
        interval_key = data[6:]
        if interval_key not in SCHEDULE_OPTIONS:
            return

        # Remove any existing scheduled job for this user
        job_name = f"schedule_{uid}"
        current_jobs = ctx.application.job_queue.get_jobs_by_name(job_name)
        for job in current_jobs:
            job.schedule_removal()

        s = read_settings(uid)
        s["schedule"] = interval_key
        # BUG-70: persist metadata needed for restart recovery
        s["schedule_chat_id"] = q.message.chat_id
        s["schedule_uname"] = uname
        s["schedule_name"] = name
        write_settings(uid, s)

        if interval_key == "off":
            await q.message.reply_text("⏰ Scheduled download *disabled*.", parse_mode="Markdown")
        else:
            interval = SCHEDULE_OPTIONS[interval_key]
            ctx.application.job_queue.run_repeating(
                _scheduled_job,
                interval=interval,
                first=interval,
                name=job_name,
                data={
                    "uid": uid,
                    "chat_id": q.message.chat_id,
                    "uname": uname,
                    "name": name,
                },
            )
            await q.message.reply_text(
                f"⏰ Scheduled download set to *every {interval_key}*.",
                parse_mode="Markdown",
            )
        await send_menu(q.message, uid, uname, name)
        return

    # ── export sources (button) ──────────────────────────────────────────────
    if data == "m_export":
        lines: list[str] = []
        for p in PLATFORMS:
            urls = read_profiles(uid, p)
            if urls:
                lines.append(f"# {p.upper()}")
                lines.extend(urls)
                lines.append("")
        if not lines:
            await q.message.reply_text("ℹ️ No sources to export.")
            return
        content = "\n".join(lines)
        export_file = udir(uid) / "cuhibot_sources.txt"
        export_file.write_text(content, encoding="utf-8")
        try:
            with open(export_file, "rb") as fh:
                await q.message.reply_document(
                    document=fh,
                    filename="cuhibot_sources.txt",
                    caption="📎 Your sources — re-upload this file to import.",
                )
        finally:
            export_file.unlink(missing_ok=True)
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
        # BUG-E: always write from env var — env vars are the source of truth on Railway.
        # Per-user uploaded cookies in cdir() take priority at runtime via resolve_cookie().
        # Detect base64: use strict validation to avoid false positives
        try:
            decoded = base64.b64decode(value, validate=True).decode("utf-8")
            # Heuristic: valid cookie files have tab-separated fields or
            # start with the Netscape header comment
            if "\t" in decoded or decoded.lstrip().startswith("#"):
                content = decoded
            else:
                content = value  # decoded but doesn't look like cookies
        except Exception:
            content = value   # treat as plain Netscape text
        try:
            dest.write_text(content, encoding="utf-8")
            logger.info("Wrote %s from env %s", cookie_filename, env_key)
        except Exception as exc:
            logger.error("Failed to write %s: %s", cookie_filename, exc)


async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only command: shows bot stats and active users."""
    uid, uname, name = _user(update)
    if not _is_admin(uid):
        await update.message.reply_text("🔒 Admin access required.")
        return

    mode = "🔓 Open (all users)" if not ALLOWED_USERS else f"🔒 Restricted ({len(ALLOWED_USERS)} users)"
    text = (
        "🛡️ *Admin Panel*\n\n"
        f"• Access mode : {mode}\n"
        f"• Admin IDs   : {len(ADMIN_IDS)}\n"
        f"• Active now  : {len(ACTIVE_USERS)}\n"
        f"• Rate limit  : {RATE_LIMIT_SECONDS}s\n"
        f"• Max profiles: {MAX_PROFILES_PER_PLATFORM}/platform\n"
        f"• Max cookie  : {MAX_COOKIE_FILE_BYTES // 1024} KB\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ── /link — single post download ─────────────────────────────────────────────

def _detect_platform(url: str) -> str | None:
    """Detect which platform a URL belongs to by checking its domain."""
    try:
        domain = urlparse(url).netloc.lower()
    except Exception:
        return None
    for plat, domains in PLATFORM_DOMAINS.items():
        if any(domain == d or domain.endswith('.' + d) for d in domains):
            return plat
    return None


async def cmd_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/link <url> — download a single post immediately without adding it as a source."""
    uid, uname, name = _user(update)
    if not _is_allowed(uid):
        await update.message.reply_text("🔒 Access denied.")
        return

    if not ctx.args:
        await update.message.reply_text(
            "📎 *Usage:* `/link <url>`\n\n"
            "Example:\n`/link https://www.instagram.com/p/ABC123/`",
            parse_mode="Markdown",
        )
        return

    url = ctx.args[0].strip()
    platform = _detect_platform(url)
    if not platform:
        await update.message.reply_text(
            "❌ URL not recognized. Supported: Instagram, TikTok, Facebook, X."
        )
        return

    ok, err = validate_url(url, platform)
    if not ok:
        await update.message.reply_text(f"❌ {err}")
        return

    if uid in ACTIVE_USERS:
        await update.message.reply_text("⚠️ A download is already running. Tap 🚫 Stop first.")
        return

    allowed, remaining = _check_rate_limit(uid)
    if not allowed:
        await update.message.reply_text(f"⏳ Please wait {remaining}s before starting another download.")
        return

    _record_download_time(uid)

    handle = handle_from_url(url)
    ch = get_channel(uid)
    target = (ctx.bot, ch) if ch else update.message
    cookie = resolve_cookie(uid, platform)
    _, _, sleep = PLATFORMS[platform]

    first = await update.message.reply_text(
        f"📎 *Downloading:* `{handle}`…", parse_mode="Markdown"
    )
    status = Status(first)

    ev = asyncio.Event()
    STOP_EVENTS[uid] = ev
    ACTIVE_USERS.add(uid)

    async def _do_link(stop: asyncio.Event) -> None:
        try:
            n = await realtime_download(
                target=target, uid=uid, platform=platform, handle=handle,
                mode="documents", url=url, cookie=cookie, sleep=sleep,
                stop=stop, status=status,
            )
            if n > 0:
                append_history(uid, {
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "platform": platform, "user": handle,
                    "media": "link", "sent": n,
                })
            if n == 0 and not stop.is_set():
                await status.set("ℹ️ *No media found.* (may be private or already downloaded)", force=True)
            elif stop.is_set():
                await status.set(f"⏹️ *Stopped.* {n} file(s) sent.", force=True)
            else:
                await status.set(f"✅ *Done!* {n} file(s) sent.", force=True)
        finally:
            _release(uid, stop)
            wipe_downloads(uid)
            await send_menu(update.message, uid, uname, name)

    task = asyncio.create_task(_do_link(ev))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


# ── /export — export all sources as a text file ──────────────────────────────

async def cmd_export(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/export — send all sources as a downloadable text file."""
    uid, uname, name = _user(update)
    if not _is_allowed(uid):
        await update.message.reply_text("🔒 Access denied.")
        return

    lines: list[str] = []
    for p in PLATFORMS:
        urls = read_profiles(uid, p)
        if urls:
            lines.append(f"# {p.upper()}")
            lines.extend(urls)
            lines.append("")

    if not lines:
        await update.message.reply_text("ℹ️ No sources to export. Add some first!")
        return

    content = "\n".join(lines)
    export_file = udir(uid) / "cuhibot_sources.txt"
    export_file.write_text(content, encoding="utf-8")
    try:
        with open(export_file, "rb") as fh:
            await update.message.reply_document(
                document=fh,
                filename="cuhibot_sources.txt",
                caption="📎 Your Cuhi Bot sources — re-upload this file to import.",
            )
    finally:
        export_file.unlink(missing_ok=True)


def _import_sources(uid: int, text: str) -> tuple[int, int]:
    """Parse an exported sources file and import URLs. Returns (added, skipped).
    BUG-55: batches reads/writes per platform to avoid N+1 disk I/O.
    """
    added = 0
    skipped = 0
    # Group URLs by platform first, then batch-write once per platform
    pending: dict[str, list[str]] = {}
    current_platform: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Header like "# INSTAGRAM"
        if line.startswith("#"):
            tag = line.lstrip("#").strip().lower()  # BUG-72: strip only '#' then whitespace
            if tag in PLATFORMS:
                current_platform = tag
            continue
        # It's a URL line
        if current_platform and _URL_RE.match(line):
            ok, _ = validate_url(line, current_platform)
            if not ok:
                skipped += 1
                continue
            if current_platform not in pending:
                pending[current_platform] = []
            pending[current_platform].append(line)
        else:
            skipped += 1

    # Batch-write per platform: one read + one write per platform
    for plat, new_urls in pending.items():
        existing = read_profiles(uid, plat)
        existing_set = set(existing)
        for url in new_urls:
            if url in existing_set:
                skipped += 1
                continue
            if len(existing) >= MAX_PROFILES_PER_PLATFORM:
                skipped += 1
                continue
            existing.append(url)
            existing_set.add(url)
            added += 1
        write_profiles(uid, plat, existing)

    return added, skipped


# ── Scheduled auto-download ──────────────────────────────────────────────────

SCHEDULE_OPTIONS = {
    "6h": 6 * 3600,
    "12h": 12 * 3600,
    "24h": 24 * 3600,
    "off": 0,
}


async def _scheduled_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback fired by JobQueue. Runs a full download for the user.
    BUG-71: wrapped in try/except to prevent unhandled exceptions from
    killing the repeating job permanently.
    """
    uid = ctx.job.data["uid"]
    chat_id = ctx.job.data["chat_id"]
    uname = ctx.job.data.get("uname", "unknown")
    name = ctx.job.data.get("name", "User")

    if uid in ACTIVE_USERS:
        return  # skip if already running

    if not any(read_profiles(uid, p) for p in PLATFORMS):
        return  # no sources

    ev = asyncio.Event()
    STOP_EVENTS[uid] = ev
    ACTIVE_USERS.add(uid)

    ch = get_channel(uid)
    target = (ctx.bot, ch) if ch else (ctx.bot, chat_id)

    try:
        first = await ctx.bot.send_message(
            chat_id=chat_id,
            text="⏰ *Scheduled download starting…*",
            parse_mode="Markdown",
        )
        status = Status(first)
        total = 0

        for platform, (_, _, sleep) in PLATFORMS.items():
            if ev.is_set():
                break
            urls = read_profiles(uid, platform)
            if not urls:
                continue
            cookie = resolve_cookie(uid, platform)
            for url in urls:
                if ev.is_set():
                    break
                handle = handle_from_url(url)
                for m in ("photos", "videos"):
                    if ev.is_set():
                        break
                    n = await realtime_download(
                        target=target, uid=uid, platform=platform,
                        handle=handle, mode=m, url=url, cookie=cookie,
                        sleep=sleep, stop=ev, status=status,
                    )
                    total += n
                    if n > 0:
                        append_history(uid, {
                            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "platform": platform, "user": handle,
                            "media": m, "sent": n,
                        })

        await status.set(
            f"⏰ *Scheduled download done!* {total} file(s).", force=True
        )
    except Exception:
        # BUG-71: log but suppress — don't let exceptions kill the repeating job
        logger.exception("Scheduled job failed for uid=%s", uid)
    finally:
        _release(uid, ev)
        wipe_downloads(uid)


async def _restore_schedules(app: Application) -> None:
    """BUG-61: Restore scheduled jobs on startup by scanning persisted settings.
    Called as Application.post_init callback after the bot is ready.
    BUG-75: per-user try/except so one corrupt file doesn't block all restorations.
    """
    if not DATA_ROOT.exists():
        return
    restored = 0
    for user_dir in DATA_ROOT.iterdir():
        if not user_dir.is_dir() or not user_dir.name.isdigit():
            continue
        uid = int(user_dir.name)
        try:
            s = read_settings(uid)
            interval_key = s.get("schedule", "off")
            chat_id = s.get("schedule_chat_id")
            if interval_key == "off" or interval_key not in SCHEDULE_OPTIONS or not chat_id:
                continue
            interval = SCHEDULE_OPTIONS[interval_key]
            if interval <= 0:
                continue
            job_name = f"schedule_{uid}"
            app.job_queue.run_repeating(
                _scheduled_job,
                interval=interval,
                first=interval,
                name=job_name,
                data={
                    "uid": uid,
                    "chat_id": chat_id,
                    "uname": s.get("schedule_uname", "unknown"),
                    "name": s.get("schedule_name", "User"),
                },
            )
            restored += 1
        except Exception:
            logger.exception("Failed to restore schedule for uid=%s", uid)
    if restored:
        logger.info("Restored %d scheduled job(s) from persistent settings", restored)


def main() -> None:
    if TOKEN == "YOUR_BOT_TOKEN":
        logger.critical("BOT_TOKEN not set! Set the BOT_TOKEN environment variable.")
        return

    bootstrap_env_cookies()

    # Log security configuration at startup
    if ALLOWED_USERS:
        logger.info("Security: restricted mode — %d allowed users", len(ALLOWED_USERS))
    else:
        logger.info("Security: open mode — all users allowed (set ALLOWED_USERS to restrict)")
    if ADMIN_IDS:
        logger.info("Security: %d admin(s) configured", len(ADMIN_IDS))

    from telegram.request import HTTPXRequest
    request = HTTPXRequest(
        connect_timeout=15.0,
        read_timeout=30.0,
        write_timeout=60.0,      # large video uploads need time
        pool_timeout=60.0,       # increased to wait longer for an available connection
        connection_pool_size=200 # increased pool size to allow more concurrent uploads
    )
    app = (
        Application.builder()
        .token(TOKEN)
        .request(request)
        .post_init(_restore_schedules)  # BUG-61: restore scheduled jobs on startup
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("menu",    cmd_start))
    app.add_handler(CommandHandler("cleanup", cmd_cleanup))
    app.add_handler(CommandHandler("admin",   cmd_admin))
    app.add_handler(CommandHandler("link",    cmd_link))
    app.add_handler(CommandHandler("export",  cmd_export))

    # Inline buttons
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Document upload (cookies)
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Text / URLs
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
