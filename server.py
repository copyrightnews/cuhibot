"""
Cuhi Bot — FastAPI backend for Telegram Mini App
Runs on PORT env var (Railway injects this), default 8080
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

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")
DATA_ROOT   = Path(os.environ.get("DATA_ROOT",   "./data"))
COOKIES_ROOT= Path(os.environ.get("COOKIES_ROOT","./cookies"))

PLATFORMS = ["instagram", "tiktok", "facebook", "x"]
COOKIE_NAMES = {
    "instagram": "instagram.com_cookies.txt",
    "tiktok":    "tiktok.com_cookies.txt",
    "facebook":  "facebook.com_cookies.txt",
    "x":         "x.com_cookies.txt",
}

app = FastAPI(title="Cuhi Bot API", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Telegram initData validation ──────────────────────────────────────────────
def validate_init_data(init_data: str) -> Optional[dict]:
    """Returns parsed user dict if valid, None if invalid."""
    if not init_data or not BOT_TOKEN:
        return None
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None

        data_check = "\n".join(
            f"{k}={v}" for k, v in sorted(parsed.items())
        )
        secret_key = hmac.new(
            b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256
        ).digest()
        expected = hmac.new(
            secret_key, data_check.encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected, received_hash):
            return None

        user_json = parsed.get("user", "{}")
        return json.loads(user_json)
    except Exception:
        return None

def get_uid(request: Request) -> int:
    """Extract and validate uid from Telegram initData header."""
    init_data = request.headers.get("X-Init-Data", "")
    
    # ── Dev/browser bypass ───────────────────────────────────────────
    # When opened directly in browser (not via Telegram), initData is empty.
    # Use ADMIN_IDS first user as fallback UID for testing.
    if not init_data:
        admin_raw = os.environ.get("ADMIN_IDS", "").strip()
        admin_ids = [x.strip() for x in admin_raw.split(",") if x.strip().isdigit()]
        if admin_ids:
            return int(admin_ids[0])
        raise HTTPException(status_code=401, detail="Missing X-Init-Data")
    # ─────────────────────────────────────────────────────────────────

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
    total = sum(
        f.stat().st_size for f in path.rglob("*") if f.is_file()
    )
    return round(total / (1024 * 1024), 2)

def disk_info():
    usage = shutil.disk_usage("/")
    return {
        "total_gb": round(usage.total / (1024**3), 1),
        "used_gb":  round(usage.used  / (1024**3), 1),
        "free_gb":  round(usage.free  / (1024**3), 1),
        "percent":  round(usage.used / usage.total * 100, 1),
    }

# ── Active download state (in-memory) ─────────────────────────────────────────
_active: dict[int, dict] = {}  # uid -> {running, platform, handle, started_at}

# ── Serve Mini App HTML ───────────────────────────────────────────────────────
@app.get("/")
async def serve_app():
    html_path = Path(__file__).parent / "app.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")
    return JSONResponse({"status": "Cuhi Bot API running"})

# ── API endpoints ─────────────────────────────────────────────────────────────
@app.get("/api/stats")
async def api_stats(request: Request):
    uid = get_uid(request)
    s = read_json(settings_path(uid), {})
    history = read_json(history_path(uid), [])

    total_profiles = 0
    for p in PLATFORMS:
        pf = udir(uid) / f"{p}_profiles.txt"
        if pf.exists():
            total_profiles += len([l for l in pf.read_text().splitlines() if l.strip()])

    cookies_ok = []
    for p in PLATFORMS:
        uc = cdir(uid) / COOKIE_NAMES[p]
        gc = COOKIES_ROOT / "_global" / COOKIE_NAMES[p]
        if uc.exists() or gc.exists():
            cookies_ok.append(p)

    return {
        "uid": uid,
        "total_profiles": total_profiles,
        "total_sent": s.get("total_sent_files", 0),
        "total_mb": round(s.get("total_bytes", 0) / (1024 * 1024), 1),
        "channel": s.get("channel"),
        "schedule": s.get("schedule"),
        "cookies": cookies_ok,
        "history_count": len(history),
        "download_active": uid in _active and _active[uid].get("running", False),
    }

@app.get("/api/disk")
async def api_disk(request: Request):
    uid = get_uid(request)
    dl_mb = folder_mb(udir(uid) / "downloads")
    return {**disk_info(), "user_downloads_mb": dl_mb}

@app.get("/api/sources")
async def api_sources(request: Request):
    uid = get_uid(request)
    result = {}
    for p in PLATFORMS:
        pf = udir(uid) / f"{p}_profiles.txt"
        if pf.exists():
            result[p] = [l.strip() for l in pf.read_text().splitlines() if l.strip()]
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
        raise HTTPException(400, "Invalid URL")

    pf = udir(uid) / f"{platform}_profiles.txt"
    existing = []
    if pf.exists():
        existing = [l.strip() for l in pf.read_text().splitlines() if l.strip()]
    if url in existing:
        raise HTTPException(409, "Already exists")
    if len(existing) >= 50:
        raise HTTPException(400, "Max 50 sources per platform")

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

    pf = udir(uid) / f"{platform}_profiles.txt"
    if not pf.exists():
        raise HTTPException(404, "Not found")

    existing = [l.strip() for l in pf.read_text().splitlines() if l.strip()]
    if url not in existing:
        raise HTTPException(404, "URL not found")

    existing.remove(url)
    pf.write_text("\n".join(existing) + "\n" if existing else "", encoding="utf-8")
    return {"ok": True, "count": len(existing)}

@app.get("/api/history")
async def api_history(request: Request):
    uid = get_uid(request)
    h = read_json(history_path(uid), [])
    return {"history": h[:100]}

@app.get("/api/channel")
async def api_get_channel(request: Request):
    uid = get_uid(request)
    s = read_json(settings_path(uid), {})
    return {"channel": s.get("channel")}

@app.post("/api/channel")
async def api_set_channel(request: Request):
    uid  = get_uid(request)
    body = await request.json()
    ch   = body.get("channel", "").strip()

    s = read_json(settings_path(uid), {})
    if ch in ("", "clear"):
        s.pop("channel", None)
    else:
        s["channel"] = ch
    write_json(settings_path(uid), s)
    return {"ok": True, "channel": s.get("channel")}

@app.get("/api/schedule")
async def api_get_schedule(request: Request):
    uid = get_uid(request)
    s = read_json(settings_path(uid), {})
    return {"schedule": s.get("schedule")}

@app.post("/api/schedule")
async def api_set_schedule(request: Request):
    uid  = get_uid(request)
    body = await request.json()
    cron = body.get("cron", "").strip()

    s = read_json(settings_path(uid), {})
    if cron:
        s["schedule"] = cron
    else:
        s.pop("schedule", None)
    write_json(settings_path(uid), s)
    return {"ok": True, "schedule": s.get("schedule")}

@app.get("/api/cookies")
async def api_get_cookies(request: Request):
    uid = get_uid(request)
    result = {}
    for p in PLATFORMS:
        uc = cdir(uid) / COOKIE_NAMES[p]
        gc = COOKIES_ROOT / "_global" / COOKIE_NAMES[p]
        if uc.exists():
            result[p] = {"has_cookie": True, "source": "user"}
        elif gc.exists():
            result[p] = {"has_cookie": True, "source": "global"}
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
        raise HTTPException(400, "Cookie file too large (max 1MB)")

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

@app.post("/api/download")
async def api_start_download(request: Request):
    uid  = get_uid(request)
    body = await request.json()

    if uid in _active and _active[uid].get("running"):
        raise HTTPException(409, "Download already running")

    _active[uid] = {
        "running": True,
        "platform": body.get("platform", "all"),
        "mode": body.get("mode", "both"),
        "started_at": time.time(),
        "note": "Triggered from Mini App — use bot for full control",
    }
    # NOTE: Actual gallery-dl execution must be triggered through the bot's
    # asyncio loop. This endpoint sets state that the bot polls.
    # For now we return the trigger state — wiring to bot loop TBD.
    return {"ok": True, "message": "Download queued. Check bot chat for progress."}

@app.post("/api/download/stop")
async def api_stop_download(request: Request):
    uid = get_uid(request)
    if uid in _active:
        _active[uid]["running"] = False
    return {"ok": True}

@app.get("/api/download/status")
async def api_download_status(request: Request):
    uid = get_uid(request)
    state = _active.get(uid, {})
    return {
        "running":    state.get("running", False),
        "platform":   state.get("platform"),
        "mode":       state.get("mode"),
        "started_at": state.get("started_at"),
    }

# ── Path helpers (module-level for reuse) ─────────────────────────────────────
def settings_path(uid: int) -> Path:
    return DATA_ROOT / str(uid) / "settings.json"

def history_path(uid: int) -> Path:
    return DATA_ROOT / str(uid) / "history.json"

# ── Entrypoint ────────────────────────────────────────────────────────────────
def start(port: int = None):
    port = port or int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
