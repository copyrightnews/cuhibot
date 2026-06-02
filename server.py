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
import secrets
import sys

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
                if (
                    _v.startswith('"')
                    and not _v.endswith('"')
                    and not (
                        _v.endswith('"') and len(_v) > 1 and _v[-2] == "\\"
                    )
                ):
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

# Automatic Cloudflare Tunnel URL discovery from tunnel.log for local development
try:
    from pathlib import Path as _EnvPath

    _tunnel_log_path = _EnvPath(__file__).parent / "tunnel.log"
    if _tunnel_log_path.exists():
        _log_content = _tunnel_log_path.read_text(errors="ignore")
        import re as _re

        _match = _re.search(
            r"https://([a-zA-Z0-9\-]+\.trycloudflare\.com)", _log_content
        )
        if _match:
            _domain_found = _match.group(1)
            if not os.environ.get("PUBLIC_DOMAIN"):
                os.environ["PUBLIC_DOMAIN"] = _domain_found
            if not os.environ.get("RAILWAY_PUBLIC_DOMAIN"):
                os.environ["RAILWAY_PUBLIC_DOMAIN"] = _domain_found
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
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DATA_ROOT = Path(os.environ.get("DATA_ROOT", "./data"))
COOKIES_ROOT = Path(os.environ.get("COOKIES_ROOT", "./cookies"))
APP_DIR = Path(__file__).parent
PLATFORMS = ["instagram", "tiktok", "facebook", "x"]

# Cookie filename mapping (mirrors bot.py)
COOKIE_FILE = {
    "instagram": "instagram.com_cookies.txt",
    "tiktok": "tiktok.com_cookies.txt",
    "facebook": "facebook.com_cookies.txt",
    "x": "x.com_cookies.txt",
}

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cuhi.server")

# ── Auth ──────────────────────────────────────────────────────────────

from file_utils import validate_file_path, check_disk_space

from contextlib import contextmanager
import time


@contextmanager
def locked_file(target: Path):
    """TOCTOU-safe, OS-level file lock wrapper that works on both Windows and Linux/Unix.
    Matches the locking mechanism of bot.py to prevent race conditions.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_suffix(target.suffix + ".lock")
    fp = open(lock_path, "a+", encoding="utf-8")
    try:
        if sys.platform == "win32":
            import msvcrt
            acquired = False
            for attempt in range(100):
                try:
                    fp.seek(0)
                    msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except (OSError, IOError):
                    time.sleep(0.02)
            if not acquired:
                raise TimeoutError(f"Could not acquire Windows OS-level lock on {target}")
        else:
            import fcntl
            acquired = False
            for attempt in range(100):
                try:
                    fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except (OSError, IOError):
                    time.sleep(0.02)
            if not acquired:
                raise TimeoutError(f"Could not acquire Unix OS-level lock on {target}")
        yield
    finally:
        try:
            if sys.platform == "win32":
                import msvcrt
                fp.seek(0)
                msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        finally:
            fp.close()
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass



def read_json_direct(path: Path, default=None):
    if default is None:
        default = {}
    try:
        with locked_file(path):
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def write_json_direct(path: Path, data):
    with locked_file(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )


def _validate_init_data(init_data: str) -> dict:
    """
    Validate Telegram WebApp initData HMAC-SHA256.
    Returns user dict if valid. Raises HTTPException(401) if invalid/missing.
    """
    if not init_data:
        raise HTTPException(
            status_code=401, detail="Open this app inside Telegram"
        )

    parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Missing hash in initData")

    data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret = hmac.new(
        b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256
    ).digest()
    expected = hmac.new(
        secret, data_check.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, received_hash):
        raise HTTPException(
            status_code=401, detail="Invalid initData signature"
        )

    try:
        return json.loads(parsed.get("user", "{}"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=401, detail="Malformed user data")


async def get_uid(request: Request) -> int:
    """FastAPI dependency: extract validated user_id from X-Init-Data."""
    init_data = request.headers.get("X-Init-Data", "")
    if init_data:
        user = _validate_init_data(init_data)
        uid = user.get("id")
        if uid:
            return int(uid)

    raise HTTPException(
        status_code=401, detail="Open this app inside Telegram"
    )


async def get_user(request: Request) -> dict:
    """Like get_uid but returns full user dict."""
    init_data = request.headers.get("X-Init-Data", "")
    if init_data:
        return _validate_init_data(init_data)

    raise HTTPException(
        status_code=401, detail="Open this app inside Telegram"
    )


# ── File helpers ──────────────────────────────────────────────────────


def user_dir(uid: int) -> Path:
    return DATA_ROOT / str(uid)


def user_cookies_dir(uid: int) -> Path:
    return COOKIES_ROOT / str(uid)


def read_json(path: Path, default=None):
    return read_json_direct(path, default)


def write_json(path: Path, data):
    return write_json_direct(path, data)


def read_profiles(uid: int, platform: str) -> list[str]:
    """Read profile URLs for a given platform."""
    p = user_dir(uid) / f"{platform}_profiles.txt"
    with locked_file(p):
        if not p.exists():
            return []
        lines = [
            line.strip() for line in p.read_text(encoding="utf-8").splitlines()
        ]
    return [line for line in lines if line and not line.startswith("#")]


def write_profiles(uid: int, platform: str, urls: list[str]):
    p = user_dir(uid) / f"{platform}_profiles.txt"
    with locked_file(p):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(urls), encoding="utf-8")


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
            item.get("user")
            or item.get("source")
            or item.get("url")
            or item.get("profile")
            or item.get("handle")
            or item.get("source_url")
            or item.get("account")
            or ""
        )
        result.append(
            {
                **item,
                "source": source,  # always present
            }
        )
    return result  # already newest-first from bot.py's insert(0, ...)


def clear_history(uid: int) -> None:
    hf = user_dir(uid) / "history.json"
    write_json(hf, [])


def get_active_cookies(uid: int) -> list[str]:
    ck_dir = user_cookies_dir(uid)
    active = []
    for plat, fname in COOKIE_FILE.items():
        enc_fname = fname.replace('.txt', '.enc')
        if (ck_dir / enc_fname).exists() or (ck_dir / fname).exists():
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


# ── FastAPI app & CORS Configuration ─────────────────────────────────────
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi.responses import JSONResponse

# Initialize rate limiter
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200/hour"],  # Global default
    storage_uri="memory://",
)

app = FastAPI(docs_url=None, redoc_url=None)
app.state.limiter = limiter

@app.exception_handler(RateLimitExceeded)
async def custom_rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """Custom rate limit exceeded response."""
    log.warning(
        "Rate limit exceeded for %s on %s",
        get_remote_address(request),
        request.url.path
    )
    retry_after = "60"
    if exc.detail and "Retry after" in exc.detail:
        try:
            retry_after = exc.detail.split("Retry after ")[1].split(" ")[0]
        except Exception:
            pass
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limit_exceeded",
            "message": "Too many requests. Please try again later.",
            "retry_after": f"{retry_after} seconds"
        },
        headers={"Retry-After": retry_after}
    )

# Build dynamic allowed origins list
allowed_origins = [
    "http://localhost",
    "https://localhost",
    "capacitor://localhost",
    "http://localhost:5173",
    "http://localhost:8080",
    "http://localhost:3000",
    "http://localhost:5000",
    "http://localhost:8000"
]

public_domain = os.environ.get("PUBLIC_DOMAIN", "").strip()
if public_domain:
    clean_domain = public_domain
    if "://" in clean_domain:
        clean_domain = clean_domain.split("://", 1)[1]
    clean_domain = clean_domain.rstrip("/")
    allowed_origins.append(f"https://{clean_domain}")
    allowed_origins.append(f"http://{clean_domain}")

# Support custom CORS origins via env for production deployments
cors_custom = os.environ.get("CORS_ALLOWED_ORIGINS", "").strip()
if cors_custom:
    for origin in cors_custom.split(","):
        origin_clean = origin.strip()
        if origin_clean:
            allowed_origins.append(origin_clean)

# Secure CORS: In production, exclude credentials for localhost origins to prevent CSRF risks.
is_prod = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("ENV") == "production")
if is_prod:
    allowed_origins = [orig for orig in allowed_origins if "localhost" not in orig]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=r"^https://copyrightnews\.github\.io$",
    allow_credentials=True,
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


@app.get("/healthz")
async def healthz():
    # 1. Check disk space
    try:
        if not check_disk_space(DATA_ROOT, required_mb=50):
            raise HTTPException(503, "Low disk space (< 50 MB)")
        
        # Calculate free space for response
        import shutil
        usage = shutil.disk_usage(DATA_ROOT if DATA_ROOT.exists() else Path("/"))
        free_mb = usage.free / (1024 * 1024)
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(503, f"Disk space check failed: {e}")

    # 2. Check write permission on DATA_ROOT
    try:
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        test_file = DATA_ROOT / ".healthz_write_test"
        test_file.touch()
        test_file.unlink()
    except Exception as e:
        raise HTTPException(503, f"DATA_ROOT is not writable: {e}")

    # 3. Check write permission on COOKIES_ROOT
    try:
        COOKIES_ROOT.mkdir(parents=True, exist_ok=True)
        test_file = COOKIES_ROOT / ".healthz_write_test"
        test_file.touch()
        test_file.unlink()
    except Exception as e:
        raise HTTPException(503, f"COOKIES_ROOT is not writable: {e}")

    return {
        "status": "ok",
        "disk_free_mb": f"{free_mb:.2f}",
        "write_checks": "passed"
    }


# ── Stats ─────────────────────────────────────────────────────────────


@app.get("/api/stats")
async def stats(user: dict = Depends(get_user)):
    uid = int(user["id"])
    s = get_settings(uid)

    sources_count = sum(len(read_profiles(uid, p)) for p in PLATFORMS)
    hist = read_history(uid, limit=10000)
    cookies = get_active_cookies(uid)

    # files_sent and downloaded_mb from settings if bot tracks them
    files_sent = s.get("files_sent", 0)
    downloaded_mb = s.get("downloaded_mb", 0)

    running = (user_dir(uid) / "download_running").exists()

    return {
        "sources": sources_count,
        "files_sent": files_sent,
        "downloaded_mb": round(downloaded_mb, 1),
        "history_count": len(hist),
        "channel": s.get("channel") or s.get("output_channel") or "",
        "schedule": {
            "cron": s.get("schedule_cron"),
            "enabled": s.get("schedule_enabled", False),
        },
        "cookies_active": cookies,
        "download_running": running,
        "username": user.get("username", ""),
        "first_name": user.get("first_name", ""),
        "email": user.get("email", ""),
        "id": uid,
    }


# ── Sources ───────────────────────────────────────────────────────────


@app.get("/api/sources")
async def list_sources(uid: int = Depends(get_uid)):
    result = []
    for plat in PLATFORMS:
        urls = read_profiles(uid, plat)
        for url in urls:
            result.append({"url": url, "platform": plat, "file_count": None})
    return result


@app.post("/api/sources", status_code=201)
@limiter.limit("20/minute")
async def add_source(request: Request, body: SourceAdd, uid: int = Depends(get_uid)):
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
@limiter.limit("20/minute")
async def delete_source(request: Request, platform: str, url: str, uid: int = Depends(get_uid)):
    plat = platform.lower()
    if plat not in PLATFORMS:
        raise HTTPException(400, f"Unknown platform: {plat}")
    decoded = urllib.parse.unquote(url)
    urls = read_profiles(uid, plat)
    if decoded not in urls:
        raise HTTPException(404, "Source not found")
    urls.remove(decoded)
    write_profiles(uid, plat, urls)
    return {"deleted": decoded}


# ── Cookies ───────────────────────────────────────────────────────────


@app.get("/api/cookies")
async def list_cookies(uid: int = Depends(get_uid)):
    ck_dir = user_cookies_dir(uid)
    result = []
    for plat, fname in COOKIE_FILE.items():
        enc_fname = fname.replace('.txt', '.enc')
        has = (ck_dir / enc_fname).exists() or (ck_dir / fname).exists()
        result.append({"platform": plat, "has_cookie": has})
    return result


@app.post("/api/cookies")
@limiter.limit("5/minute")
async def set_cookie(request: Request, body: CookieSet, uid: int = Depends(get_uid)):
    if body.platform not in COOKIE_FILE:
        raise HTTPException(400, f"Unknown platform: {body.platform}")
    ck_dir = user_cookies_dir(uid)
    ck_dir.mkdir(parents=True, exist_ok=True)
    
    enc_fname = COOKIE_FILE[body.platform].replace('.txt', '.enc')
    ck_path = ck_dir / enc_fname
    old_path = ck_dir / COOKIE_FILE[body.platform]
    
    try:
        from crypto_utils import get_crypto
        crypto = get_crypto()
        with locked_file(ck_path):
            crypto.save_encrypted_cookie(ck_path, body.cookie_data)
        
        # Safely remove old plaintext file if it exists
        if old_path.exists():
            try:
                old_path.unlink(missing_ok=True)
            except Exception:
                pass
                
        return {"platform": body.platform, "status": "saved"}
    except Exception as e:
        log.exception("Cookie encryption failed for uid=%s: %s", uid, e)
        raise HTTPException(500, f"Failed to encrypt cookie: {e}")


@app.delete("/api/cookies/{platform}")
@limiter.limit("5/minute")
async def delete_cookie(request: Request, platform: str, uid: int = Depends(get_uid)):
    if platform not in COOKIE_FILE:
        raise HTTPException(400, f"Unknown platform: {platform}")
    
    ck_dir = user_cookies_dir(uid)
    enc_fname = COOKIE_FILE[platform].replace('.txt', '.enc')
    enc_path = ck_dir / enc_fname
    old_path = ck_dir / COOKIE_FILE[platform]
    
    with locked_file(enc_path):
        if enc_path.exists():
            enc_path.unlink()
            
    if old_path.exists():
        with locked_file(old_path):
            if old_path.exists():
                old_path.unlink()
                
    return {"deleted": platform}



# ── History ───────────────────────────────────────────────────────────


@app.get("/api/history")
async def get_history(limit: int = 100, uid: int = Depends(get_uid)):
    return read_history(uid, min(limit, 500))


@app.delete("/api/history")
@limiter.limit("20/minute")
async def delete_history(request: Request, uid: int = Depends(get_uid)):
    clear_history(uid)
    return {"ok": True, "message": "History cleared"}


# ── Channel ───────────────────────────────────────────────────────────


@app.get("/api/channel")
async def get_channel(uid: int = Depends(get_uid)):
    s = get_settings(uid)
    return {"channel_id": s.get("channel") or s.get("output_channel") or ""}


def normalize_chat(value) -> int | str:
    """Convert user-entered channel strings into valid Telegram chat IDs."""
    if isinstance(value, int):
        if not (-1000000000000 <= value <= 1000000000000):
            raise HTTPException(400, "Telegram ID out of range")
        return value
    v = str(value).strip()
    if v.startswith("@"):
        if len(v) > 33 or len(v) < 2:
            raise HTTPException(400, "Invalid Telegram username")
        return v
    if v.lstrip("-").isdigit():
        n = int(v)
        if not (-1000000000000 <= n <= 1000000000000):
            raise HTTPException(400, "Telegram ID out of range")
        if n < 0:
            return n
        if n > 5000000000:
            return n
        res = int(f"-100{n}")
        if not (-1000000000000 <= res <= 1000000000000):
            raise HTTPException(400, "Telegram ID out of range")
        return res
    return v


@app.post("/api/channel")
@limiter.limit("20/minute")
async def set_channel(request: Request, body: ChannelSet, uid: int = Depends(get_uid)):
    normalized = normalize_chat(body.channel_id)
    set_settings(uid, {"channel": normalized, "output_channel": normalized})
    return {"channel_id": normalized}


# ── Schedule ──────────────────────────────────────────────────────────


@app.get("/api/schedule")
async def get_schedule(uid: int = Depends(get_uid)):
    s = get_settings(uid)
    return {
        "cron": s.get("schedule_cron", ""),
        "enabled": s.get("schedule_enabled", False),
    }


@app.post("/api/schedule")
@limiter.limit("20/minute")
async def set_schedule(request: Request, body: ScheduleSet, uid: int = Depends(get_uid)):
    set_settings(
        uid, {"schedule_cron": body.cron, "schedule_enabled": body.enabled}
    )
    return {"cron": body.cron, "enabled": body.enabled}


# ── Download ──────────────────────────────────────────────────────────


@app.post("/api/download")
@limiter.limit("10/minute")
async def trigger_download(request: Request, body: DownloadTrigger, uid: int = Depends(get_uid)):
    running_flag = user_dir(uid) / "download_running"
    with locked_file(running_flag):
        if running_flag.exists():
            raise HTTPException(409, "Download already running")

        # Write trigger file — bot.py polls this and starts the actual download
        trigger = {
            "media_type": body.media_type,
            "force": body.force,
            "stories": body.stories,
            "highlights": body.highlights,
        }
        trigger_path = user_dir(uid) / "download_trigger.json"
        write_json(trigger_path, trigger)

        running_flag.touch()

    return {"status": "triggered"}


@app.post("/api/download/stop")
async def stop_download(uid: int = Depends(get_uid)):
    # Write stop flag — bot.py checks this
    stop_flag = user_dir(uid) / "stop_flag"
    with locked_file(stop_flag):
        stop_flag.touch()

    # Remove running flag
    running_flag = user_dir(uid) / "download_running"
    with locked_file(running_flag):
        if running_flag.exists():
            running_flag.unlink()

    return {"status": "stopped"}


@app.get("/api/download/status")
async def download_status(uid: int = Depends(get_uid)):
    running_flag = user_dir(uid) / "download_running"
    with locked_file(running_flag):
        flag_running = running_flag.exists()
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
        "total_gb": round(u.total / 1e9, 1),
        "used_gb": round(u.used / 1e9, 1),
        "free_gb": round(u.free / 1e9, 1),
        "percent_used": pct,
    }





# ── Entry point ───────────────────────────────────────────────────────

# Clear any stale download_running flags on boot
if DATA_ROOT.exists():
    for run_flag in DATA_ROOT.glob("*/download_running"):
        try:
            run_flag.unlink(missing_ok=True)
        except Exception:
            pass


def validate_environment() -> None:
    """Validate critical environment variables at startup to prevent failures or misconfiguration."""
    import re
    token = os.environ.get("BOT_TOKEN")
    if not token or token == "YOUR_BOT_TOKEN":
        raise ValueError("BOT_TOKEN is not set or is still set to placeholder")
    
    if not re.match(r"^\d+:[A-Za-z0-9_-]+$", token):
        raise ValueError("BOT_TOKEN format is invalid. Must be in the format '123456789:ABC...'")

    encryption_key = os.environ.get("COOKIE_ENCRYPTION_KEY")
    if not encryption_key:
        raise ValueError("COOKIE_ENCRYPTION_KEY is not set in environment. Please generate a 32-byte key.")
        
    try:
        from cryptography.fernet import Fernet
        Fernet(encryption_key.encode("utf-8"))
    except Exception as e:
        raise ValueError(f"COOKIE_ENCRYPTION_KEY is invalid or malformed: {e}")


def start(port: int = 8080):
    """Called from bot.py background thread."""
    try:
        validate_environment()
    except Exception as e:
        log.critical("CRITICAL: Environment validation failed: %s", e)
        import sys
        sys.exit(f"CRITICAL ERROR: Environment validation failed: {e}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    try:
        validate_environment()
    except Exception as e:
        log.critical("CRITICAL: Environment validation failed: %s", e)
        import sys
        sys.exit(f"CRITICAL ERROR: Environment validation failed: {e}")
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
