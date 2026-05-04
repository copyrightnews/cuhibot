import os
import json
import hmac
import hashlib
import shutil
import urllib.parse
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import HTMLResponse
import uvicorn

app = FastAPI()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
DATA_ROOT = Path(os.environ.get("DATA_ROOT", "./data"))
COOKIES_ROOT = Path(os.environ.get("COOKIES_ROOT", "./cookies"))
PLATFORMS = ["instagram", "tiktok", "facebook", "x"]

def validate_init_data(init_data: str) -> dict:
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing init data")
    
    try:
        parsed = urllib.parse.parse_qsl(init_data)
        data_dict = {k: v for k, v in parsed}
        if "hash" not in data_dict:
            raise HTTPException(status_code=401, detail="No hash in init data")
        
        hash_val = data_dict.pop("hash")
        
        # Sort keys
        sorted_keys = sorted(data_dict.keys())
        data_check_string = "\n".join([f"{k}={data_dict[k]}" for k in sorted_keys])
        
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if calc_hash != hash_val:
            raise HTTPException(status_code=401, detail="Invalid hash")
            
        user_data = json.loads(data_dict.get("user", "{}"))
        return user_data
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Auth error: {str(e)}")

def get_uid(request: Request) -> int:
    init_data = request.headers.get("X-Init-Data", "")
    user = validate_init_data(init_data)
    uid = user.get("id")
    if not uid:
        raise HTTPException(status_code=401, detail="No user ID")
    return int(uid)

def udir(uid: int) -> Path:
    p = DATA_ROOT / str(uid)
    p.mkdir(parents=True, exist_ok=True)
    return p

def cdir(uid: int) -> Path:
    p = COOKIES_ROOT / str(uid)
    p.mkdir(parents=True, exist_ok=True)
    return p

@app.get("/")
async def serve_app():
    if not os.path.exists("app.html"):
        return HTMLResponse("app.html not found", status_code=404)
    with open("app.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/api/stats")
async def get_stats(request: Request):
    uid = get_uid(request)
    
    # Sources
    sources_count = 0
    for p in PLATFORMS:
        profile_file = udir(uid) / f"{p}_profiles.txt"
        if profile_file.exists():
            lines = [line.strip() for line in profile_file.read_text(encoding="utf-8").splitlines() if line.strip()]
            sources_count += len(lines)
            
    # Downloaded count
    settings_file = udir(uid) / "settings.json"
    downloaded = 0
    channel = None
    cron = "off"
    if settings_file.exists():
        try:
            s = json.loads(settings_file.read_text(encoding="utf-8"))
            downloaded = s.get("total_sent_files", 0)
            channel = s.get("channel", None)
            cron = s.get("schedule", "off")
        except:
            pass
            
    # Cookies
    cookies = []
    for p in PLATFORMS:
        cfile = cdir(uid) / f"{p}.com_cookies.txt"
        if cfile.exists():
            cookies.append(p)
            
    running = (udir(uid) / "download_running").exists()
    
    return {
        "sources_count": sources_count,
        "downloaded_count": downloaded,
        "cookie_platforms": cookies,
        "channel": channel,
        "schedule_cron": cron,
        "download_running": running
    }

@app.get("/api/sources")
async def get_sources(request: Request):
    uid = get_uid(request)
    sources = []
    for p in PLATFORMS:
        profile_file = udir(uid) / f"{p}_profiles.txt"
        if profile_file.exists():
            lines = [line.strip() for line in profile_file.read_text(encoding="utf-8").splitlines() if line.strip()]
            for line in lines:
                sources.append({"id": urllib.parse.quote(line, safe=""), "url": line, "platform": p})
    return sources

@app.post("/api/sources")
async def add_source(request: Request):
    uid = get_uid(request)
    data = await request.json()
    url = data.get("url")
    platform = data.get("platform")
    if not url or platform not in PLATFORMS:
        raise HTTPException(status_code=400, detail="Invalid url or platform")
        
    profile_file = udir(uid) / f"{platform}_profiles.txt"
    lines = []
    if profile_file.exists():
        lines = [line.strip() for line in profile_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if url not in lines:
        lines.append(url)
        profile_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"status": "ok"}

@app.delete("/api/sources/{platform}/{encoded_url}")
async def delete_source(request: Request, platform: str, encoded_url: str):
    uid = get_uid(request)
    if platform not in PLATFORMS:
        raise HTTPException(status_code=400, detail="Invalid platform")
    url = urllib.parse.unquote(encoded_url)
    profile_file = udir(uid) / f"{platform}_profiles.txt"
    if profile_file.exists():
        lines = [line.strip() for line in profile_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        if url in lines:
            lines.remove(url)
            profile_file.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
    return {"status": "ok"}

@app.get("/api/cookies")
async def get_cookies(request: Request):
    uid = get_uid(request)
    res = []
    for p in PLATFORMS:
        cfile = cdir(uid) / f"{p}.com_cookies.txt"
        res.append({"platform": p, "has_cookie": cfile.exists()})
    return res

@app.post("/api/cookies")
async def set_cookie(request: Request):
    uid = get_uid(request)
    data = await request.json()
    platform = data.get("platform")
    cookie_data = data.get("cookie_data", "")
    if platform not in PLATFORMS:
        raise HTTPException(status_code=400, detail="Invalid platform")
    cfile = cdir(uid) / f"{platform}.com_cookies.txt"
    cfile.write_text(cookie_data, encoding="utf-8")
    return {"status": "ok"}

@app.delete("/api/cookies/{platform}")
async def delete_cookie(request: Request, platform: str):
    uid = get_uid(request)
    if platform not in PLATFORMS:
        raise HTTPException(status_code=400, detail="Invalid platform")
    cfile = cdir(uid) / f"{platform}.com_cookies.txt"
    if cfile.exists():
        cfile.unlink()
    return {"status": "ok"}

@app.get("/api/history")
async def get_history(request: Request):
    uid = get_uid(request)
    hfile = udir(uid) / "history.json"
    if hfile.exists():
        try:
            return json.loads(hfile.read_text(encoding="utf-8"))[:100]
        except:
            return []
    return []

@app.get("/api/channel")
async def get_channel_api(request: Request):
    uid = get_uid(request)
    settings_file = udir(uid) / "settings.json"
    if settings_file.exists():
        try:
            s = json.loads(settings_file.read_text(encoding="utf-8"))
            return {"channel": s.get("channel")}
        except:
            pass
    return {"channel": None}

@app.post("/api/channel")
async def post_channel_api(request: Request):
    uid = get_uid(request)
    data = await request.json()
    channel = data.get("channel_id")
    settings_file = udir(uid) / "settings.json"
    s = {}
    if settings_file.exists():
        try:
            s = json.loads(settings_file.read_text(encoding="utf-8"))
        except:
            pass
    if channel:
        s["channel"] = channel
    else:
        s.pop("channel", None)
    settings_file.write_text(json.dumps(s, indent=2), encoding="utf-8")
    return {"status": "ok"}

@app.get("/api/schedule")
async def get_schedule(request: Request):
    uid = get_uid(request)
    settings_file = udir(uid) / "settings.json"
    if settings_file.exists():
        try:
            s = json.loads(settings_file.read_text(encoding="utf-8"))
            cron = s.get("schedule", "off")
            return {"cron": cron, "enabled": cron != "off"}
        except:
            pass
    return {"cron": "off", "enabled": False}

@app.post("/api/schedule")
async def post_schedule(request: Request):
    uid = get_uid(request)
    data = await request.json()
    cron = data.get("cron", "off")
    enabled = data.get("enabled", False)
    if not enabled:
        cron = "off"
    
    settings_file = udir(uid) / "settings.json"
    s = {}
    if settings_file.exists():
        try:
            s = json.loads(settings_file.read_text(encoding="utf-8"))
        except:
            pass
    s["schedule"] = cron
    settings_file.write_text(json.dumps(s, indent=2), encoding="utf-8")
    return {"status": "ok"}

@app.post("/api/download")
async def start_download(request: Request):
    uid = get_uid(request)
    data = await request.json()
    
    trigger_file = udir(uid) / "download_trigger.json"
    trigger_file.write_text(json.dumps(data), encoding="utf-8")
    return {"status": "triggered"}

@app.post("/api/download/stop")
async def stop_download(request: Request):
    uid = get_uid(request)
    stop_file = udir(uid) / "stop_flag"
    stop_file.touch()
    return {"status": "stopped"}

@app.get("/api/download/status")
async def check_download_status(request: Request):
    uid = get_uid(request)
    running = (udir(uid) / "download_running").exists()
    return {"running": running}

@app.get("/api/disk")
async def check_disk(request: Request):
    try:
        total, used, free = shutil.disk_usage(DATA_ROOT)
        return {
            "total_gb": round(total / (1024**3), 2),
            "used_gb": round(used / (1024**3), 2),
            "free_gb": round(free / (1024**3), 2),
            "percent_used": round(used / total * 100, 1)
        }
    except:
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent_used": 0}

def start(port: int):
    uvicorn.run(app, host="0.0.0.0", port=port)
