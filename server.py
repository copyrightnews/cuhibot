"""
server.py — Cuhi Bot Mini App backend (v2)
FastAPI + uvicorn, runs alongside bot.py in a daemon thread.

Fixes vs v1:
  - Strict initData auth: 401 for all /api/* when no valid initData
  - No anonymous fallback - each user sees ONLY their own data
  - History: reads multiple possible key names for source URL
  - Disk: always real-time shutil call
  - /api/stats: richer response (username, cookies_active, files_sent, etc.)
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import shutil
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# ── Config ────────────────────────────────────────────────────────────
BOT_TOKEN    = os.environ["BOT_TOKEN"]
DATA_ROOT    = Path(os.environ.get("DATA_ROOT",    "./data"))
COOKIES_ROOT = Path(os.environ.get("COOKIES_ROOT", "./cookies"))
APP_DIR      = Path(__file__).parent
PLATFORMS    = ["instagram", "tiktok", "facebook", "x"]

# Cookie filename mapping (mirrors bot.py)
COOKIE_FILE = {
    "instagram": "instagram.com_cookies.txt",
    "tiktok":    "tiktok.com_cookies.txt",
    "facebook":  "facebook.com_cookies.txt",
    "x":         "x.com_cookies.txt",
}

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cuhi.server")

# ── Auth ──────────────────────────────────────────────────────────────

def _validate_init_data(init_data: str) -> dict:
    """
    Validate Telegram WebApp initData HMAC-SHA256.
    Returns user dict if valid. Raises HTTPException(401) if invalid/missing.
    """
    if not init_data:
        raise HTTPException(status_code=401, detail="Open this app inside Telegram")

    parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Missing hash in initData")

    data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, received_hash):
        raise HTTPException(status_code=401, detail="Invalid initData signature")

    try:
        return json.loads(parsed.get("user", "{}"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=401, detail="Malformed user data")


async def get_uid(request: Request) -> int:
    """FastAPI dependency: extract validated user_id from X-Init-Data header."""
    init_data = request.headers.get("X-Init-Data", "")
    user = _validate_init_data(init_data)
    uid = user.get("id")
    if not uid:
        raise HTTPException(status_code=401, detail="No user ID in initData")
    return int(uid)


async def get_user(request: Request) -> dict:
    """Like get_uid but returns full user dict."""
    init_data = request.headers.get("X-Init-Data", "")
    return _validate_init_data(init_data)


# ── File helpers ──────────────────────────────────────────────────────

def user_dir(uid: int) -> Path:
    return DATA_ROOT / str(uid)

def user_cookies_dir(uid: int) -> Path:
    return COOKIES_ROOT / str(uid)

def read_json(path: Path, default=None):
    if default is None:
        default = {}
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def read_profiles(uid: int, platform: str) -> list[str]:
    """Read profile URLs for a given platform."""
    p = user_dir(uid) / f"{platform}_profiles.txt"
    if not p.exists():
        return []
    lines = [l.strip() for l in p.read_text(encoding="utf-8").splitlines()]
    return [l for l in lines if l and not l.startswith("#")]

def write_profiles(uid: int, platform: str, urls: list[str]):
    p = user_dir(uid) / f"{platform}_profiles.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(urls), encoding="utf-8")

def count_downloaded_files(uid: int) -> int:
    """Count files in user download dirs."""
    total = 0
    dl_root = user_dir(uid) / "downloads"
    if dl_root.exists():
        total += sum(1 for f in dl_root.rglob("*") if f.is_file())
    return total

def get_settings(uid: int) -> dict:
    return read_json(user_dir(uid) / "settings.json")

def set_settings(uid: int, data: dict):
    s = get_settings(uid)
    s.update(data)
    write_json(user_dir(uid) / "settings.json", s)

def read_history(uid: int, limit: int = 100) -> list[dict]:
    raw = read_json(user_dir(uid) / "history.json", default=[])
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        # Some versions store as {entries: [...]}
        items = raw.get("entries", raw.get("items", []))
    else:
        return []
    # Normalize: ensure each item exposes a "source" field
    result = []
    for item in items[-limit:]:
        if not isinstance(item, dict):
            continue
        # Try every possible key name for the source URL
        source = (
            item.get("source") or item.get("url") or item.get("profile") or
            item.get("handle") or item.get("source_url") or item.get("account") or ""
        )
        result.append({
            **item,
            "source": source,  # always present
        })
    return list(reversed(result))  # newest first

def get_active_cookies(uid: int) -> list[str]:
    ck_dir = user_cookies_dir(uid)
    active = []
    for plat, fname in COOKIE_FILE.items():
        if (ck_dir / fname).exists():
            active.append(plat)
    return active

# ── Pydantic models ───────────────────────────────────────────────────

class SourceAdd(BaseModel):
    url: str
    platform: str = "instagram"

class CookieSet(BaseModel):
    platform: str
    cookie_data: str

class DownloadTrigger(BaseModel):
    media_type: str = "all"
    force: bool = False
    stories: bool = False
    highlights: bool = False

class ChannelSet(BaseModel):
    channel_id: str

class ScheduleSet(BaseModel):
    cron: str
    enabled: bool = True

# ── Active downloads tracker ──────────────────────────────────────────
_active_procs: dict[int, asyncio.subprocess.Process] = {}

# ── FastAPI app ───────────────────────────────────────────────────────
app = FastAPI(docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static ────────────────────────────────────────────────────────────

@app.get("/")
async def serve_app():
    html = APP_DIR / "app.html"
    if not html.exists():
        raise HTTPException(404, "app.html not found")
    return FileResponse(html, media_type="text/html")

# ── Stats ─────────────────────────────────────────────────────────────

@app.get("/api/stats")
async def stats(user: dict = Depends(get_user)):
    uid = int(user["id"])
    s   = get_settings(uid)

    sources_count = sum(len(read_profiles(uid, p)) for p in PLATFORMS)
    hist          = read_history(uid, limit=10000)
    cookies       = get_active_cookies(uid)

    # files_sent and downloaded_mb from settings if bot tracks them
    files_sent    = s.get("files_sent", 0)
    downloaded_mb = s.get("downloaded_mb", 0)

    proc = _active_procs.get(uid)
    running = proc is not None and proc.returncode is None

    return {
        "sources":        sources_count,
        "files_sent":     files_sent,
        "downloaded_mb":  round(downloaded_mb, 1),
        "history_count":  len(hist),
        "channel":        s.get("channel") or s.get("output_channel") or "",
        "schedule":       {"cron": s.get("schedule_cron"), "enabled": s.get("schedule_enabled", False)},
        "cookies_active": cookies,
        "download_running": running,
        "username":       user.get("username", ""),
    }

# ── Sources ───────────────────────────────────────────────────────────

@app.get("/api/sources")
async def list_sources(uid: int = Depends(get_uid)):
    result = []
    for plat in PLATFORMS:
        urls = read_profiles(uid, plat)
        for url in urls:
            # Count files downloaded for this profile
            dl_dir = user_dir(uid) / "downloads"
            result.append({"url": url, "platform": plat, "file_count": None})
    return result

@app.post("/api/sources", status_code=201)
async def add_source(body: SourceAdd, uid: int = Depends(get_uid)):
    plat = body.platform.lower()
    if plat not in PLATFORMS:
        raise HTTPException(400, f"Unknown platform: {plat}")
    urls = read_profiles(uid, plat)
    if body.url in urls:
        raise HTTPException(409, "Source already exists")
    urls.append(body.url)
    write_profiles(uid, plat, urls)
    return {"url": body.url, "platform": plat}

@app.delete("/api/sources/{platform}/{url:path}")
async def delete_source(platform: str, url: str, uid: int = Depends(get_uid)):
    decoded = urllib.parse.unquote(url)
    urls = read_profiles(uid, platform)
    if decoded not in urls:
        raise HTTPException(404, "Source not found")
    urls.remove(decoded)
    write_profiles(uid, platform, urls)
    return {"deleted": decoded}

# ── Cookies ───────────────────────────────────────────────────────────

@app.get("/api/cookies")
async def list_cookies(uid: int = Depends(get_uid)):
    ck_dir = user_cookies_dir(uid)
    result = []
    for plat, fname in COOKIE_FILE.items():
        has = (ck_dir / fname).exists()
        result.append({"platform": plat, "has_cookie": has})
    return result

@app.post("/api/cookies")
async def set_cookie(body: CookieSet, uid: int = Depends(get_uid)):
    if body.platform not in COOKIE_FILE:
        raise HTTPException(400, f"Unknown platform: {body.platform}")
    ck_dir = user_cookies_dir(uid)
    ck_dir.mkdir(parents=True, exist_ok=True)
    (ck_dir / COOKIE_FILE[body.platform]).write_text(body.cookie_data, encoding="utf-8")
    return {"platform": body.platform, "status": "saved"}

@app.delete("/api/cookies/{platform}")
async def delete_cookie(platform: str, uid: int = Depends(get_uid)):
    if platform not in COOKIE_FILE:
        raise HTTPException(400, f"Unknown platform: {platform}")
    ck_path = user_cookies_dir(uid) / COOKIE_FILE[platform]
    if ck_path.exists():
        ck_path.unlink()
    return {"deleted": platform}

# ── History ───────────────────────────────────────────────────────────

@app.get("/api/history")
async def get_history(limit: int = 100, uid: int = Depends(get_uid)):
    return read_history(uid, min(limit, 500))

# ── Channel ───────────────────────────────────────────────────────────

@app.get("/api/channel")
async def get_channel(uid: int = Depends(get_uid)):
    s = get_settings(uid)
    return {"channel_id": s.get("channel") or s.get("output_channel") or ""}

@app.post("/api/channel")
async def set_channel(body: ChannelSet, uid: int = Depends(get_uid)):
    set_settings(uid, {"channel": body.channel_id, "output_channel": body.channel_id})
    return {"channel_id": body.channel_id}

# ── Schedule ──────────────────────────────────────────────────────────

@app.get("/api/schedule")
async def get_schedule(uid: int = Depends(get_uid)):
    s = get_settings(uid)
    return {"cron": s.get("schedule_cron", ""), "enabled": s.get("schedule_enabled", False)}

@app.post("/api/schedule")
async def set_schedule(body: ScheduleSet, uid: int = Depends(get_uid)):
    set_settings(uid, {"schedule_cron": body.cron, "schedule_enabled": body.enabled})
    return {"cron": body.cron, "enabled": body.enabled}

# ── Download ──────────────────────────────────────────────────────────

@app.post("/api/download")
async def trigger_download(body: DownloadTrigger, uid: int = Depends(get_uid)):
    proc = _active_procs.get(uid)
    if proc and proc.returncode is None:
        raise HTTPException(409, "Download already running")

    # Write trigger file — bot.py polls this and starts the actual download
    trigger = {
        "media_type": body.media_type,
        "force":      body.force,
        "stories":    body.stories,
        "highlights": body.highlights,
    }
    trigger_path = user_dir(uid) / "download_trigger.json"
    write_json(trigger_path, trigger)

    # Also write running flag
    (user_dir(uid) / "download_running").touch()

    return {"status": "triggered"}

@app.post("/api/download/stop")
async def stop_download(uid: int = Depends(get_uid)):
    # Write stop flag — bot.py checks this
    (user_dir(uid) / "stop_flag").touch()

    proc = _active_procs.get(uid)
    if proc and proc.returncode is None:
        proc.terminate()
        _active_procs.pop(uid, None)

    # Remove running flag
    running_flag = user_dir(uid) / "download_running"
    if running_flag.exists():
        running_flag.unlink()

    return {"status": "stopped"}

@app.get("/api/download/status")
async def download_status(uid: int = Depends(get_uid)):
    proc = _active_procs.get(uid)
    proc_running = proc is not None and proc.returncode is None
    flag_running = (user_dir(uid) / "download_running").exists()
    return {"running": proc_running or flag_running}

# ── Disk (always real-time) ───────────────────────────────────────────

@app.get("/api/disk")
async def disk_info(uid: int = Depends(get_uid)):
    path = user_dir(uid) if user_dir(uid).exists() else DATA_ROOT
    if not path.exists():
        path = Path("/")
    u = shutil.disk_usage(path)
    pct = round(u.used / u.total * 100, 1) if u.total else 0
    return {
        "total_gb":    round(u.total / 1e9, 1),
        "used_gb":     round(u.used  / 1e9, 1),
        "free_gb":     round(u.free  / 1e9, 1),
        "percent_used": pct,
    }

# ── Entry point ───────────────────────────────────────────────────────

def start(port: int = 8080):
    """Called from bot.py background thread."""
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
