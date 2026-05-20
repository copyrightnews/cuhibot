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

import hashlib
import hmac
import json
import logging
import os
# Load .env file manually if exists (no-dependency dotenv fallback supporting multi-line strings)
try:
    from pathlib import Path as _EnvPath
    _env_path = _EnvPath(__file__).parent / ".env"
    if _env_path.exists():
        _content = _env_path.read_text(encoding="utf-8")
        _lines = _content.splitlines()
        _i = 0
        while _i < len(_lines):
            _line = _lines[_i].strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                _k = _k.strip()
                _v = _v.strip()
                if _v.startswith('"') and not _v.endswith('"') and not (_v.endswith('"') and len(_v) > 1 and _v[-2] == '\\'):
                    _val = [_v[1:]]
                    _i += 1
                    while _i < len(_lines):
                        _l = _lines[_i]
                        if _l.endswith('"') and not _l.endswith('\\"'):
                            _val.append(_l[:-1])
                            break
                        else:
                            _val.append(_l)
                        _i += 1
                    _v = "\n".join(_val)
                elif _v.startswith('"') and _v.endswith('"'):
                    _v = _v[1:-1]
                elif _v.startswith("'") and _v.endswith("'"):
                    _v = _v[1:-1]
                os.environ[_k] = _v
            _i += 1
except Exception:
    pass
import shutil
import urllib.parse
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ── Config ────────────────────────────────────────────────────────────
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
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

# ── Auth & Session Store ──────────────────────────────────────────────
SESSIONS_FILE = DATA_ROOT / "sessions.json"

def read_json_direct(path: Path, default=None):
    if default is None:
        default = {}
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def write_json_direct(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def get_sessions() -> dict:
    return read_json_direct(SESSIONS_FILE, default={})

def validate_token(token: str) -> dict | None:
    sessions = get_sessions()
    return sessions.get(token)

def _validate_init_data(init_data: str) -> dict:
    """
    Validate Telegram WebApp initData HMAC-SHA256 or standalone App Token.
    Returns user dict if valid. Raises HTTPException(401) if invalid/missing.
    """
    if not init_data:
        raise HTTPException(status_code=401, detail="Open this app inside Telegram or log in")

    # Native Android App Token bypass: format "UID:HMAC"
    if ":" in init_data and "hash=" not in init_data:
        try:
            uid_str, signature = init_data.split(":", 1)
            expected = hmac.new(b"AppToken", f"{uid_str}:{BOT_TOKEN}".encode(), hashlib.sha256).hexdigest()
            if hmac.compare_digest(expected, signature):
                return {"id": int(uid_str), "first_name": "App User", "username": "app"}
        except Exception:
            pass
        raise HTTPException(status_code=401, detail="Invalid App Token")

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
    """FastAPI dependency: extract validated user_id from Bearer token or X-Init-Data."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        session = validate_token(token)
        if session:
            return int(session["id"])

    init_data = request.headers.get("X-Init-Data", "")
    if init_data:
        try:
            user = _validate_init_data(init_data)
            uid = user.get("id")
            if uid:
                return int(uid)
        except Exception:
            pass

    raise HTTPException(status_code=401, detail="Open this app inside Telegram or log in")


async def get_user(request: Request) -> dict:
    """Like get_uid but returns full user dict."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        session = validate_token(token)
        if session:
            return session

    init_data = request.headers.get("X-Init-Data", "")
    if init_data:
        return _validate_init_data(init_data)

    raise HTTPException(status_code=401, detail="Open this app inside Telegram or log in")


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
    lines = [line.strip() for line in p.read_text(encoding="utf-8").splitlines()]
    return [line for line in lines if line and not line.startswith("#")]

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
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        # Try every possible key name for the source URL, including 'user' from bot.py
        source = (
            item.get("user") or item.get("source") or item.get("url") or item.get("profile") or
            item.get("handle") or item.get("source_url") or item.get("account") or ""
        )
        result.append({
            **item,
            "source": source,  # always present
        })
    return result  # already newest-first from bot.py's insert(0, ...)

def clear_history(uid: int) -> None:
    hf = user_dir(uid) / "history.json"
    write_json(hf, [])

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
    client: str = "telegram"

class ChannelSet(BaseModel):
    channel_id: str

class ScheduleSet(BaseModel):
    cron: str
    enabled: bool = True

# ── FastAPI app ───────────────────────────────────────────────────────
app = FastAPI(docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)



# ── Static ────────────────────────────────────────────────────

@app.get("/")
async def serve_app():
    html = APP_DIR / "app.html"
    if not html.exists():
        raise HTTPException(404, "app.html not found")
    return FileResponse(html, media_type="text/html")

@app.get("/logo.jpg")
async def serve_logo():
    logo = APP_DIR / "logo.jpg"
    if not logo.exists():
        raise HTTPException(404, "logo.jpg not found")
    return FileResponse(logo, media_type="image/jpeg")

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

    running = (user_dir(uid) / "download_running").exists()

    return {
        "sources":        sources_count,
        "files_sent":     files_sent,
        "downloaded_mb":  round(downloaded_mb, 1),
        "history_count":  len(hist),
        "channel":        s.get("channel") or s.get("output_channel") or "",
        "schedule":       {"cron": s.get("schedule_cron"), "enabled": s.get("schedule_enabled", False)},
        "cookies_active": cookies,
        "download_running": running,
        "files_waiting":  count_downloaded_files(uid),
        "username":       user.get("username", ""),
        "first_name":     user.get("first_name", ""),
        "email":          user.get("email", ""),
        "id":             uid,
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
    urls.insert(0, body.url)
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

@app.delete("/api/history")
async def delete_history(uid: int = Depends(get_uid)):
    clear_history(uid)
    return {"ok": True, "message": "History cleared"}

# ── Channel ───────────────────────────────────────────────────────────

@app.get("/api/channel")
async def get_channel(uid: int = Depends(get_uid)):
    s = get_settings(uid)
    return {"channel_id": s.get("channel") or s.get("output_channel") or ""}

def normalize_chat(value) -> str:
    """Convert user-entered channel strings into valid Telegram chat IDs."""
    v = str(value).strip()
    if v.startswith("@"):
        return v
    if v.lstrip("-").isdigit():
        n = int(v)
        if n < 0:
            return str(n)
        if n > 5000000000:
            return str(n)
        return f"-100{n}"
    return v

@app.post("/api/channel")
async def set_channel(body: ChannelSet, uid: int = Depends(get_uid)):
    normalized = normalize_chat(body.channel_id)
    set_settings(uid, {"channel": normalized, "output_channel": normalized})
    return {"channel_id": normalized}

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
    if (user_dir(uid) / "download_running").exists():
        raise HTTPException(409, "Download already running")

    # Write trigger file — bot.py polls this and starts the actual download
    trigger = {
        "media_type": body.media_type,
        "force":      body.force,
        "stories":    body.stories,
        "highlights": body.highlights,
        "client":     body.client,
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

    # Remove running flag
    running_flag = user_dir(uid) / "download_running"
    if running_flag.exists():
        running_flag.unlink()

    return {"status": "stopped"}

@app.get("/api/download/status")
async def download_status(uid: int = Depends(get_uid)):
    flag_running = (user_dir(uid) / "download_running").exists()
    return {"running": flag_running}

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

# ── Files (For Android Native Download) ───────────────────────────────

@app.get("/api/files")
async def list_files(uid: int = Depends(get_uid)):
    dl_dir = user_dir(uid) / "downloads"
    if not dl_dir.exists():
        return []
    
    MEDIA_EXTS = {
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
        ".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v"
    }
    files = []
    for f in dl_dir.rglob("*"):
        if f.is_file() and f.suffix.lower() in MEDIA_EXTS:
            rel_path = f.relative_to(dl_dir)
            files.append({
                "path": str(rel_path).replace("\\", "/"),
                "name": f.name,
                "size": f.stat().st_size
            })
    return files

@app.get("/api/files/{file_path:path}")
async def get_file(file_path: str, uid: int = Depends(get_uid)):
    dl_dir = user_dir(uid) / "downloads"
    target = dl_dir / file_path
    
    # Security: prevent directory traversal
    try:
        target.resolve().relative_to(dl_dir.resolve())
    except ValueError:
        raise HTTPException(403, "Access denied")
        
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "File not found")
        
    return FileResponse(target)

@app.delete("/api/files")
async def clear_files(uid: int = Depends(get_uid)):
    dl_dir = user_dir(uid) / "downloads"
    if dl_dir.exists():
        shutil.rmtree(dl_dir, ignore_errors=True)
    return {"status": "cleared"}

@app.delete("/api/files/{file_path:path}")
async def delete_file(file_path: str, uid: int = Depends(get_uid)):
    dl_dir = user_dir(uid) / "downloads"
    target = dl_dir / file_path
    
    try:
        target.resolve().relative_to(dl_dir.resolve())
    except ValueError:
        raise HTTPException(403, "Access denied")
        
    if target.exists() and target.is_file():
        target.unlink()
    return {"status": "deleted"}

# ── Entry point ───────────────────────────────────────────────────────

# Clear any stale download_running flags on boot
if DATA_ROOT.exists():
    for run_flag in DATA_ROOT.glob("*/download_running"):
        try:
            run_flag.unlink(missing_ok=True)
        except Exception:
            pass

def start(port: int = 8080):
    """Called from bot.py background thread."""
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
