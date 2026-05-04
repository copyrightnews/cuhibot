"""
Cuhi Bot — FastAPI backend for Telegram Mini App
Serves app.html at / and provides /api/* endpoints.
Runs on $PORT (Railway injects this automatically).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import time
import urllib.parse
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

# ── Config ─────────────────────────────────────────────────────────────────
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
DATA_ROOT    = Path(os.environ.get("DATA_ROOT",    "./data"))
COOKIES_ROOT = Path(os.environ.get("COOKIES_ROOT", "./cookies"))

_ADMIN_RAW = os.environ.get("ADMIN_IDS", "").strip()
ADMIN_IDS: list[int] = [
    int(x.strip()) for x in _ADMIN_RAW.split(",") if x.strip().isdigit()
]

PLATFORMS = ["instagram", "tiktok", "facebook", "x"]
PROFILE_FILES = {
    "instagram": "instagram_profiles.txt",
    "tiktok":    "tiktok_profiles.txt",
    "facebook":  "facebook_profiles.txt",
    "x":         "x_profiles.txt",
}
COOKIE_NAMES = {
    "instagram": "instagram.com_cookies.txt",
    "tiktok":    "tiktok.com_cookies.txt",
    "facebook":  "facebook.com_cookies.txt",
    "x":         "x.com_cookies.txt",
}

# ── App setup ───────────────────────────────────────────────────────────────
app = FastAPI(title="Cuhi Bot API", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── In-memory download state ─────────────────────────────────────────────────
_active: dict[int, dict] = {}  # uid -> {running, platform, mode, started_at}

# ── Telegram initData validation ─────────────────────────────────────────────
def validate_init_data(init_data: str) -> Optional[dict]:
    """Returns parsed user dict if HMAC valid, None otherwise."""
    if not init_data or not BOT_TOKEN:
        return None
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected   = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received_hash):
            return None
        return json.loads(parsed.get("user", "{}"))
    except Exception:
        return None


def get_uid(request: Request) -> int:
    """
    Extract UID from Telegram initData header.
    Falls back to first ADMIN_ID when accessed from a plain browser
    (initData is empty outside Telegram WebView) — for testing only.
    """
    init_data = request.headers.get("X-Init-Data", "").strip()

    # ── Browser / dev bypass ─────────────────────────────────────────────
    if not init_data:
        if ADMIN_IDS:
            return ADMIN_IDS[0]
        raise HTTPException(status_code=401, detail="Missing X-Init-Data")
    # ─────────────────────────────────────────────────────────────────────

    user = validate_init_data(init_data)
    if not user:
        raise HTTPException(status_code=403, detail="Invalid initData")
    return int(user["id"])


# ── Path helpers ──────────────────────────────────────────────────────────────
def udir(uid: int) -> Path:
    p = DATA_ROOT / str(uid)
    p.mkdir(parents=True, exist_ok=True)
    return p

def cdir(uid: int) -> Path:
    p = COOKIES_ROOT / str(uid)
    p.mkdir(parents=True, exist_ok=True)
    return p

def settings_path(uid: int) -> Path:
    return DATA_ROOT / str(uid) / "settings.json"

def history_path(uid: int) -> Path:
    return DATA_ROOT / str(uid) / "history.json"

def read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

def folder_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return round(total / (1024 * 1024), 2)

def disk_info() -> dict:
    try:
        usage = shutil.disk_usage("/")
        return {
            "total_gb": round(usage.total / (1024**3), 1),
            "used_gb":  round(usage.used  / (1024**3), 1),
            "free_gb":  round(usage.free  / (1024**3), 1),
            "percent":  round(usage.used / usage.total * 100, 1),
        }
    except Exception:
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0}


# ── Serve Mini App HTML ───────────────────────────────────────────────────────
@app.get("/")
async def serve_app():
    html_path = Path(__file__).parent / "app.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    return JSONResponse({"status": "Cuhi Bot API — app.html not found"})

@app.get("/health")
async def health():
    return {"ok": True, "ts": int(time.time())}


# ── Stats ─────────────────────────────────────────────────────────────────────
@app.get("/api/stats")
async def api_stats(request: Request):
    uid = get_uid(request)
    s       = read_json(settings_path(uid), {})
    history = read_json(history_path(uid),  [])

    # Count total profiles across all platforms
    total_profiles = 0
    for p in PLATFORMS:
        pf = udir(uid) / PROFILE_FILES[p]
        if pf.exists():
            lines = [l for l in pf.read_text(encoding="utf-8").splitlines() if l.strip()]
            total_profiles += len(lines)

    # Which platforms have cookies
    cookies_ok = []
    for p in PLATFORMS:
        uc = cdir(uid) / COOKIE_NAMES[p]
        gc = COOKIES_ROOT / "_global" / COOKIE_NAMES[p]
        if uc.exists() or gc.exists():
            cookies_ok.append(p)

    dl_state = _active.get(uid, {})

    return {
        "uid":             uid,
        "total_profiles":  total_profiles,
        "total_sent":      s.get("total_sent_files", 0),
        "total_mb":        round(s.get("total_bytes", 0) / (1024 * 1024), 1),
        "channel":         s.get("channel"),
        "schedule":        s.get("schedule"),
        "cookies":         cookies_ok,
        "history_count":   len(history),
        "download_active": dl_state.get("running", False),
        "download_platform": dl_state.get("platform"),
    }


# ── Disk ──────────────────────────────────────────────────────────────────────
@app.get("/api/disk")
async def api_disk(request: Request):
    uid    = get_uid(request)
    dl_mb  = folder_mb(udir(uid) / "downloads")
    return {**disk_info(), "user_downloads_mb": dl_mb}


# ── Sources ───────────────────────────────────────────────────────────────────
@app.get("/api/sources")
async def api_sources(request: Request):
    uid    = get_uid(request)
    result = {}
    for p in PLATFORMS:
        pf = udir(uid) / PROFILE_FILES[p]
        if pf.exists():
            result[p] = [l.strip() for l in pf.read_text(encoding="utf-8").splitlines() if l.strip()]
        else:
            result[p] = []
    return result

@app.post("/api/sources")
async def api_add_source(request: Request):
    uid  = get_uid(request)
    body = await request.json()
    platform = body.get("platform", "").lower()
    url      = body.get("url", "").strip()

    if platform not in PLATFORMS:
        raise HTTPException(400, "Invalid platform")
    if not url.startswith("http"):
        raise HTTPException(400, "Invalid URL — must start with http")

    pf = udir(uid) / PROFILE_FILES[platform]
    existing = []
    if pf.exists():
        existing = [l.strip() for l in pf.read_text(encoding="utf-8").splitlines() if l.strip()]
    if url in existing:
        raise HTTPException(409, "Source already exists")
    if len(existing) >= 50:
        raise HTTPException(400, "Max 50 sources per platform reached")

    existing.append(url)
    pf.write_text("\n".join(existing) + "\n", encoding="utf-8")
    return {"ok": True, "count": len(existing)}

@app.delete("/api/sources")
async def api_delete_source(request: Request):
    uid  = get_uid(request)
    body = await request.json()
    platform = body.get("platform", "").lower()
    url      = body.get("url", "").strip()

    if platform not in PLATFORMS:
        raise HTTPException(400, "Invalid platform")

    pf = udir(uid) / PROFILE_FILES[platform]
    if not pf.exists():
        raise HTTPException(404, "No sources found")

    existing = [l.strip() for l in pf.read_text(encoding="utf-8").splitlines() if l.strip()]
    if url not in existing:
        raise HTTPException(404, "URL not in list")

    existing.remove(url)
    pf.write_text("\n".join(existing) + "\n" if existing else "", encoding="utf-8")
    return {"ok": True, "count": len(existing)}


# ── History ───────────────────────────────────────────────────────────────────
@app.get("/api/history")
async def api_history(request: Request):
    uid = get_uid(request)
    h   = read_json(history_path(uid), [])
    return {"history": h[:100]}


# ── Channel ───────────────────────────────────────────────────────────────────
@app.get("/api/channel")
async def api_get_channel(request: Request):
    uid = get_uid(request)
    s   = read_json(settings_path(uid), {})
    return {"channel": s.get("channel")}

@app.post("/api/channel")
async def api_set_channel(request: Request):
    uid  = get_uid(request)
    body = await request.json()
    ch   = body.get("channel", "").strip()
    s    = read_json(settings_path(uid), {})
    if ch in ("", "clear"):
        s.pop("channel", None)
    else:
        s["channel"] = ch
    write_json(settings_path(uid), s)
    return {"ok": True, "channel": s.get("channel")}


# ── Schedule ──────────────────────────────────────────────────────────────────
@app.get("/api/schedule")
async def api_get_schedule(request: Request):
    uid = get_uid(request)
    s   = read_json(settings_path(uid), {})
    return {"schedule": s.get("schedule")}

@app.post("/api/schedule")
async def api_set_schedule(request: Request):
    uid  = get_uid(request)
    body = await request.json()
    cron = body.get("cron", "").strip()
    s    = read_json(settings_path(uid), {})
    if cron:
        s["schedule"] = cron
    else:
        s.pop("schedule", None)
    write_json(settings_path(uid), s)
    return {"ok": True, "schedule": s.get("schedule")}


# ── Cookies ───────────────────────────────────────────────────────────────────
@app.get("/api/cookies")
async def api_get_cookies(request: Request):
    uid    = get_uid(request)
    result = {}
    for p in PLATFORMS:
        uc = cdir(uid) / COOKIE_NAMES[p]
        gc = COOKIES_ROOT / "_global" / COOKIE_NAMES[p]
        if uc.exists():
            result[p] = {"has_cookie": True,  "source": "user"}
        elif gc.exists():
            result[p] = {"has_cookie": True,  "source": "global"}
        else:
            result[p] = {"has_cookie": False, "source": None}
    return result

@app.post("/api/cookies")
async def api_set_cookie(request: Request):
    uid  = get_uid(request)
    body = await request.json()
    platform = body.get("platform", "").lower()
    content  = body.get("content", "")

    if platform not in PLATFORMS:
        raise HTTPException(400, "Invalid platform")
    if len(content.encode()) > 1_048_576:
        raise HTTPException(400, "Cookie file too large (max 1 MB)")

    cookie_path = cdir(uid) / COOKIE_NAMES[platform]
    cookie_path.write_text(content, encoding="utf-8")
    return {"ok": True}

@app.delete("/api/cookies")
async def api_delete_cookie(request: Request):
    uid  = get_uid(request)
    body = await request.json()
    platform = body.get("platform", "").lower()

    if platform not in PLATFORMS:
        raise HTTPException(400, "Invalid platform")

    cookie_path = cdir(uid) / COOKIE_NAMES[platform]
    if cookie_path.exists():
        cookie_path.unlink()
    return {"ok": True}


# ── Download control ──────────────────────────────────────────────────────────
@app.post("/api/download")
async def api_start_download(request: Request):
    uid  = get_uid(request)
    body = await request.json()

    if uid in _active and _active[uid].get("running"):
        raise HTTPException(409, "A download is already running")

    _active[uid] = {
        "running":    True,
        "platform":   body.get("platform", "all"),
        "mode":       body.get("mode",     "both"),
        "stories":    body.get("stories",  False),
        "highlights": body.get("highlights", False),
        "force":      body.get("force",    False),
        "started_at": time.time(),
        "source":     "miniapp",
    }
    # Note: actual gallery-dl execution runs in the bot's asyncio loop.
    # This endpoint queues the request; the bot polls _active and executes.
    return {
        "ok":      True,
        "message": "Download queued — check your bot chat for progress updates.",
    }

@app.post("/api/download/stop")
async def api_stop_download(request: Request):
    uid = get_uid(request)
    if uid in _active:
        _active[uid]["running"] = False
    return {"ok": True}

@app.get("/api/download/status")
async def api_download_status(request: Request):
    uid   = get_uid(request)
    state = _active.get(uid, {})
    return {
        "running":    state.get("running",    False),
        "platform":   state.get("platform"),
        "mode":       state.get("mode"),
        "started_at": state.get("started_at"),
    }


# ── Entrypoint (called from bot.py in daemon thread) ──────────────────────────
def start(port: int = None) -> None:
    port = port or int(os.environ.get("PORT", 8080))
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
        access_log=False,
    )
