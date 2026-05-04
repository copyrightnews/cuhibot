"""
Cuhi Bot — FastAPI backend for Telegram Mini App
Download sync: DATA_ROOT/{uid}/download_state.json (shared with bot.py)
Auth: X-Init-Data header (HMAC-SHA256) — falls back to ADMIN_IDS[0] in browser
"""
from __future__ import annotations
import hashlib, hmac, json, os, shutil, time, urllib.parse
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

# ══ CONFIG ═══════════════════════════════════════════════════════════════════
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
DATA_ROOT    = Path(os.environ.get("DATA_ROOT",    "./data"))
COOKIES_ROOT = Path(os.environ.get("COOKIES_ROOT", "./cookies"))
_ADMIN_RAW   = os.environ.get("ADMIN_IDS", "").strip()
ADMIN_IDS: list[int] = [int(x) for x in _ADMIN_RAW.split(",") if x.strip().isdigit()]

PLATFORMS     = ["instagram", "tiktok", "facebook", "x"]
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

app = FastAPI(title="Cuhi Bot API", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# ══ FILE-BASED DOWNLOAD STATE ═════════════════════════════════════════════════
def dl_state_path(uid: int) -> Path:
    return DATA_ROOT / str(uid) / "download_state.json"

def read_dl_state(uid: int) -> dict:
    p = dl_state_path(uid)
    if not p.exists():
        return {"running": False, "queued": False, "stop_requested": False}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"running": False, "queued": False, "stop_requested": False}

def write_dl_state(uid: int, data: dict) -> None:
    p = dl_state_path(uid)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(p)

# ══ AUTH ══════════════════════════════════════════════════════════════════════
def validate_init_data(init_data: str) -> Optional[dict]:
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
    init_data = request.headers.get("X-Init-Data", "").strip()
    if not init_data:
        # Browser dev bypass — falls back to first ADMIN_ID
        if ADMIN_IDS:
            import logging as _l
            _l.getLogger(__name__).warning("get_uid: no initData, using admin fallback uid=%s", ADMIN_IDS[0])
            return ADMIN_IDS[0]
        raise HTTPException(401, "Missing X-Init-Data")
    user = validate_init_data(init_data)
    if not user:
        raise HTTPException(403, "Invalid initData")
    
    resolved_uid = int(user["id"])
    import logging as _l
    _l.getLogger(__name__).warning("get_uid: resolved initData uid=%s", resolved_uid)
    return resolved_uid

# ══ HELPERS ═══════════════════════════════════════════════════════════════════
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
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)

def folder_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return round(total / (1024 * 1024), 2)

def disk_info() -> dict:
    try:
        u = shutil.disk_usage("/")
        return {
            "total_gb": round(u.total / 1024**3, 1),
            "used_gb":  round(u.used  / 1024**3, 1),
            "free_gb":  round(u.free  / 1024**3, 1),
            "percent":  round(u.used / u.total * 100, 1),
        }
    except Exception:
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0}

# ══ ROUTES ════════════════════════════════════════════════════════════════════
@app.get("/")
async def serve_app():
    p = Path(__file__).parent / "app.html"
    return FileResponse(p, media_type="text/html") if p.exists() else JSONResponse({"status": "ok"})

@app.get("/health")
async def health():
    return {"ok": True, "ts": int(time.time())}

@app.get("/api/stats")
async def api_stats(request: Request):
    uid = get_uid(request)
    s   = read_json(settings_path(uid), {})
    h   = read_json(history_path(uid),  [])
    dl  = read_dl_state(uid)

    total_profiles = 0
    for p in PLATFORMS:
        pf = udir(uid) / PROFILE_FILES[p]
        if pf.exists():
            total_profiles += len([l for l in pf.read_text(encoding="utf-8").splitlines() if l.strip()])

    cookies_ok = [
        p for p in PLATFORMS
        if (cdir(uid) / COOKIE_NAMES[p]).exists()
        or (COOKIES_ROOT / "_global" / COOKIE_NAMES[p]).exists()
    ]

    return {
        "uid":               uid,
        "total_profiles":    total_profiles,
        "total_sent":        s.get("total_sent_files", 0),
        "total_mb":          round(s.get("total_bytes", 0) / 1_048_576, 1),
        "channel":           s.get("channel"),
        "schedule":          s.get("schedule"),
        "cookies":           cookies_ok,
        "history_count":     len(h),
        "download_active":   dl.get("running", False) or dl.get("queued", False),
        "download_platform": dl.get("platform"),
        "download_progress": dl.get("progress", ""),
    }

@app.get("/api/disk")
async def api_disk(request: Request):
    uid = get_uid(request)
    return {**disk_info(), "user_downloads_mb": folder_mb(udir(uid) / "downloads")}

@app.get("/api/sources")
async def api_sources(request: Request):
    uid = get_uid(request)
    result = {}
    for p in PLATFORMS:
        pf = udir(uid) / PROFILE_FILES[p]
        result[p] = (
            [l.strip() for l in pf.read_text(encoding="utf-8").splitlines() if l.strip()]
            if pf.exists() else []
        )
    return result

@app.post("/api/sources")
async def api_add_source(request: Request):
    uid      = get_uid(request)
    body     = await request.json()
    platform = body.get("platform", "").lower()
    url      = body.get("url", "").strip()

    if platform not in PLATFORMS:
        raise HTTPException(400, "Invalid platform")
    if not url.startswith("http"):
        raise HTTPException(400, "URL must start with http")

    pf = udir(uid) / PROFILE_FILES[platform]
    existing = [l.strip() for l in pf.read_text(encoding="utf-8").splitlines() if l.strip()] if pf.exists() else []
    if url in existing:
        raise HTTPException(409, "Already exists")
    if len(existing) >= 50:
        raise HTTPException(400, "Max 50 sources per platform")

    existing.append(url)
    pf.write_text("\n".join(existing) + "\n", encoding="utf-8")
    return {"ok": True, "count": len(existing)}

@app.delete("/api/sources")
async def api_delete_source(request: Request):
    uid      = get_uid(request)
    body     = await request.json()
    platform = body.get("platform", "").lower()
    url      = body.get("url", "").strip()

    if platform not in PLATFORMS:
        raise HTTPException(400, "Invalid platform")

    pf = udir(uid) / PROFILE_FILES[platform]
    if not pf.exists():
        raise HTTPException(404, "No sources")

    existing = [l.strip() for l in pf.read_text(encoding="utf-8").splitlines() if l.strip()]
    if url not in existing:
        raise HTTPException(404, "URL not found")

    existing.remove(url)
    pf.write_text("\n".join(existing) + "\n" if existing else "", encoding="utf-8")
    return {"ok": True, "count": len(existing)}

@app.get("/api/history")
async def api_history(request: Request):
    uid = get_uid(request)
    return {"history": read_json(history_path(uid), [])[:100]}

@app.get("/api/channel")
async def api_get_channel(request: Request):
    return {"channel": read_json(settings_path(get_uid(request)), {}).get("channel")}

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

@app.get("/api/schedule")
async def api_get_schedule(request: Request):
    return {"schedule": read_json(settings_path(get_uid(request)), {}).get("schedule")}

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

@app.get("/api/cookies")
async def api_get_cookies(request: Request):
    uid = get_uid(request)
    result = {}
    for p in PLATFORMS:
        uc = cdir(uid)      / COOKIE_NAMES[p]
        gc = COOKIES_ROOT / "_global" / COOKIE_NAMES[p]
        result[p] = (
            {"has_cookie": True,  "source": "user"}
            if uc.exists() else
            {"has_cookie": True,  "source": "global"}
            if gc.exists() else
            {"has_cookie": False, "source": None}
        )
    return result

@app.post("/api/cookies")
async def api_set_cookie(request: Request):
    uid      = get_uid(request)
    body     = await request.json()
    platform = body.get("platform", "").lower()
    content  = body.get("content", "")

    if platform not in PLATFORMS:
        raise HTTPException(400, "Invalid platform")
    if len(content.encode()) > 1_048_576:
        raise HTTPException(400, "Too large (max 1 MB)")

    (cdir(uid) / COOKIE_NAMES[platform]).write_text(content, encoding="utf-8")
    return {"ok": True}

@app.delete("/api/cookies")
async def api_delete_cookie(request: Request):
    uid      = get_uid(request)
    body     = await request.json()
    platform = body.get("platform", "").lower()

    if platform not in PLATFORMS:
        raise HTTPException(400, "Invalid platform")

    cp = cdir(uid) / COOKIE_NAMES[platform]
    if cp.exists():
        cp.unlink()
    return {"ok": True}

# ══ DOWNLOAD CONTROL ══════════════════════════════════════════════════════════
@app.post("/api/download")
async def api_start_download(request: Request):
    uid     = get_uid(request)
    body    = await request.json()
    current = read_dl_state(uid)

    if current.get("running"):
        raise HTTPException(409, "Download already running")
    if current.get("queued"):
        raise HTTPException(409, "Download already queued")

    write_dl_state(uid, {
        "running":       False,
        "queued":        True,
        "stop_requested": False,
        "platform":      body.get("platform", "all"),
        "mode":          body.get("mode", "both"),
        "stories":       body.get("stories", False),
        "highlights":    body.get("highlights", False),
        "force":         body.get("force", False),
        "queued_at":     time.time(),
        "started_at":    None,
        "progress":      "Queued — waiting for bot…",
        "sent":          0,
        "source":        "miniapp",
    })
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "MINIAPP wrote download_state.json uid=%s path=%s", uid, str(dl_state_path(uid))
    )
    return {"ok": True, "message": "Queued — bot will start shortly. Watch your chat."}

@app.post("/api/download/stop")
async def api_stop_download(request: Request):
    uid   = get_uid(request)
    state = read_dl_state(uid)
    state["stop_requested"] = True
    state["queued"]         = False
    write_dl_state(uid, state)
    return {"ok": True}

@app.get("/api/download/status")
async def api_download_status(request: Request):
    uid = get_uid(request)
    s   = read_dl_state(uid)
    return {
        "running":    s.get("running", False),
        "queued":     s.get("queued",  False),
        "active":     s.get("running", False) or s.get("queued", False),
        "platform":   s.get("platform"),
        "mode":       s.get("mode"),
        "progress":   s.get("progress", ""),
        "sent":       s.get("sent", 0),
        "started_at": s.get("started_at"),
        "queued_at":  s.get("queued_at"),
    }

# ══ ENTRYPOINT ════════════════════════════════════════════════════════════════
def start(port: int = None) -> None:
    port = port or int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning", access_log=False)
