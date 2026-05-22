# Critical Security Fixes - Implementation Guide

This document provides ready-to-implement code fixes for the most critical security issues identified in the audit.

---

## 🔴 FIX #1: Secure Credential Management

### Problem
Credentials are hardcoded in `.env` file and committed to repository.

### Solution: Environment-Based Configuration

**Step 1: Create `.env.example` (commit this)**
```env
# Telegram Bot Configuration
BOT_TOKEN="your_bot_token_here"
ADMIN_IDS="comma_separated_admin_ids"

# Data Storage Paths
DATA_ROOT="./data"
COOKIES_ROOT="./cookies"

# Server Configuration
PORT="8080"
PUBLIC_DOMAIN="your_domain_here"

# Cookie Encryption (generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
COOKIE_ENCRYPTION_KEY="your_encryption_key_here"

# Social Media Cookies (use cookie files instead of env vars)
# Store cookies in cookies/_global/ directory
```

**Step 2: Update `.gitignore`**
```gitignore
# Environment variables
.env
.env.local
.env.production

# Sensitive data
cookies/
data/
*.log
tunnel.log

# Secrets
secrets/
*.key
*.pem
```

**Step 3: Add validation in `bot.py`**
```python
# Add at the top of bot.py after imports
def validate_environment():
    """Validate required environment variables are set."""
    required_vars = ["BOT_TOKEN", "COOKIE_ENCRYPTION_KEY"]
    missing = [var for var in required_vars if not os.environ.get(var)]
    
    if missing:
        logger.critical("Missing required environment variables: %s", missing)
        sys.exit(1)
    
    # Validate BOT_TOKEN format
    token = os.environ.get("BOT_TOKEN", "")
    if not re.match(r'^\d+:[A-Za-z0-9_-]{35}$', token):
        logger.critical("Invalid BOT_TOKEN format")
        sys.exit(1)
    
    logger.info("Environment validation passed")

# Call before starting bot
validate_environment()
```

---

## 🔴 FIX #2: Cookie Encryption

### Problem
Cookies stored in plaintext, exposing user sessions.

### Solution: Encrypt Cookies at Rest

**Step 1: Create `crypto_utils.py`**
```python
"""
Cryptographic utilities for secure data storage.
"""
import os
import logging
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from typing import Optional

logger = logging.getLogger(__name__)

class CookieEncryption:
    """Handles encryption/decryption of cookie files."""
    
    def __init__(self):
        key = os.environ.get("COOKIE_ENCRYPTION_KEY")
        if not key:
            raise ValueError("COOKIE_ENCRYPTION_KEY not set in environment")
        
        try:
            self.cipher = Fernet(key.encode())
        except Exception as e:
            raise ValueError(f"Invalid encryption key: {e}")
    
    def encrypt_cookie(self, cookie_data: str) -> bytes:
        """Encrypt cookie data."""
        try:
            return self.cipher.encrypt(cookie_data.encode('utf-8'))
        except Exception as e:
            logger.error("Cookie encryption failed: %s", e)
            raise
    
    def decrypt_cookie(self, encrypted_data: bytes) -> str:
        """Decrypt cookie data."""
        try:
            return self.cipher.decrypt(encrypted_data).decode('utf-8')
        except InvalidToken:
            logger.error("Invalid encryption token - cookie may be corrupted")
            raise
        except Exception as e:
            logger.error("Cookie decryption failed: %s", e)
            raise
    
    def save_encrypted_cookie(self, path: Path, cookie_data: str) -> None:
        """Save encrypted cookie to file."""
        encrypted = self.encrypt_cookie(cookie_data)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encrypted)
        logger.info("Encrypted cookie saved to %s", path)
    
    def load_encrypted_cookie(self, path: Path) -> Optional[str]:
        """Load and decrypt cookie from file."""
        if not path.exists():
            return None
        
        try:
            encrypted = path.read_bytes()
            return self.decrypt_cookie(encrypted)
        except Exception as e:
            logger.error("Failed to load cookie from %s: %s", path, e)
            return None

# Global instance
_crypto = None

def get_crypto() -> CookieEncryption:
    """Get or create global crypto instance."""
    global _crypto
    if _crypto is None:
        _crypto = CookieEncryption()
    return _crypto
```

**Step 2: Update `bot.py` cookie handling**
```python
from crypto_utils import get_crypto

def resolve_cookie(uid: int, platform: str) -> Path:
    """Return best available cookie file."""
    _, cookie_name, _ = PLATFORMS[platform]
    
    # Change extension to .enc for encrypted files
    encrypted_name = cookie_name.replace('.txt', '.enc')
    
    user_cookie = cdir(uid) / encrypted_name
    global_cookie = global_cookie_dir() / encrypted_name
    
    if user_cookie.exists():
        return user_cookie
    if global_cookie.exists():
        return global_cookie
    return user_cookie

def load_cookie_for_gallery_dl(uid: int, platform: str) -> Optional[Path]:
    """Load and decrypt cookie, return temp file path for gallery-dl."""
    encrypted_path = resolve_cookie(uid, platform)
    if not encrypted_path.exists():
        return None
    
    try:
        crypto = get_crypto()
        cookie_data = crypto.load_encrypted_cookie(encrypted_path)
        if not cookie_data:
            return None
        
        # Create temporary unencrypted file for gallery-dl
        temp_cookie = encrypted_path.with_suffix('.tmp')
        temp_cookie.write_text(cookie_data, encoding='utf-8')
        return temp_cookie
    except Exception as e:
        logger.error("Failed to load cookie for %s: %s", platform, e)
        return None

# Update build_gallery_dl_cmd to use decrypted temp file
def build_gallery_dl_cmd(url: str, out_dir: Path, ...) -> list[str]:
    cmd = ["gallery-dl", "--dest", str(out_dir)]
    
    # ... other args ...
    
    temp_cookie = load_cookie_for_gallery_dl(uid, platform)
    if temp_cookie:
        cmd += ["--cookies", str(temp_cookie)]
    
    return cmd

# Clean up temp cookie after use
def cleanup_temp_cookie(temp_cookie: Optional[Path]) -> None:
    """Securely delete temporary cookie file."""
    if temp_cookie and temp_cookie.exists():
        try:
            # Overwrite with random data before deletion
            temp_cookie.write_bytes(os.urandom(temp_cookie.stat().st_size))
            temp_cookie.unlink()
        except Exception as e:
            logger.warning("Failed to cleanup temp cookie: %s", e)
```

**Step 3: Update `server.py` cookie endpoints**
```python
from crypto_utils import get_crypto

@app.post("/api/cookies")
async def set_cookie(body: CookieSet, uid: int = Depends(get_uid)):
    if body.platform not in COOKIE_FILE:
        raise HTTPException(400, f"Unknown platform: {body.platform}")
    
    ck_dir = user_cookies_dir(uid)
    ck_dir.mkdir(parents=True, exist_ok=True)
    
    # Change to .enc extension
    encrypted_name = COOKIE_FILE[body.platform].replace('.txt', '.enc')
    ck_path = ck_dir / encrypted_name
    
    try:
        crypto = get_crypto()
        crypto.save_encrypted_cookie(ck_path, body.cookie_data)
        return {"platform": body.platform, "status": "saved_encrypted"}
    except Exception as e:
        logger.exception("Cookie encryption failed for uid=%s", uid)
        raise HTTPException(500, "Failed to encrypt cookie")

@app.get("/api/cookies")
async def list_cookies(uid: int = Depends(get_uid)):
    ck_dir = user_cookies_dir(uid)
    result = []
    for plat, fname in COOKIE_FILE.items():
        encrypted_name = fname.replace('.txt', '.enc')
        has = (ck_dir / encrypted_name).exists()
        result.append({"platform": plat, "has_cookie": has})
    return result
```

**Step 4: Migration script for existing cookies**
```python
"""
migrate_cookies.py - Encrypt existing plaintext cookies
"""
import os
from pathlib import Path
from crypto_utils import get_crypto

def migrate_cookies():
    """Migrate all plaintext cookies to encrypted format."""
    cookies_root = Path(os.environ.get("COOKIES_ROOT", "./cookies"))
    crypto = get_crypto()
    
    migrated = 0
    failed = 0
    
    for cookie_file in cookies_root.rglob("*.txt"):
        try:
            # Read plaintext cookie
            cookie_data = cookie_file.read_text(encoding='utf-8')
            
            # Create encrypted version
            encrypted_path = cookie_file.with_suffix('.enc')
            crypto.save_encrypted_cookie(encrypted_path, cookie_data)
            
            # Securely delete plaintext version
            cookie_file.write_bytes(os.urandom(cookie_file.stat().st_size))
            cookie_file.unlink()
            
            migrated += 1
            print(f"✓ Migrated: {cookie_file}")
        except Exception as e:
            failed += 1
            print(f"✗ Failed: {cookie_file} - {e}")
    
    print(f"\nMigration complete: {migrated} migrated, {failed} failed")

if __name__ == "__main__":
    migrate_cookies()
```

---

## 🟠 FIX #3: Rate Limiting

### Problem
No rate limiting on API endpoints allows DoS attacks.

### Solution: Implement Rate Limiting with slowapi

**Step 1: Install dependency**
```bash
pip install slowapi
```

**Step 2: Update `requirements.txt`**
```txt
python-telegram-bot[job-queue]==22.1
gallery-dl==1.32.0
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
slowapi>=0.1.9
cryptography>=42.0.0
```

**Step 3: Update `server.py`**
```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Initialize rate limiter
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100/hour"],  # Global default
    storage_uri="memory://",  # Use Redis in production
)

app = FastAPI(docs_url=None, redoc_url=None)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Apply rate limits to sensitive endpoints
@app.post("/api/download")
@limiter.limit("10/minute")  # Max 10 downloads per minute
async def trigger_download(
    request: Request,
    body: DownloadTrigger,
    uid: int = Depends(get_uid)
):
    # ... existing code ...

@app.post("/api/sources")
@limiter.limit("20/minute")  # Max 20 source additions per minute
async def add_source(
    request: Request,
    body: SourceAdd,
    uid: int = Depends(get_uid)
):
    # ... existing code ...

@app.post("/api/cookies")
@limiter.limit("5/minute")  # Max 5 cookie updates per minute
async def set_cookie(
    request: Request,
    body: CookieSet,
    uid: int = Depends(get_uid)
):
    # ... existing code ...

@app.get("/api/files/{file_path:path}")
@limiter.limit("100/minute")  # Max 100 file downloads per minute
async def get_file(
    request: Request,
    file_path: str,
    uid: int = Depends(get_uid)
):
    # ... existing code ...
```

**Step 4: Add custom rate limit handler**
```python
from fastapi.responses import JSONResponse

@app.exception_handler(RateLimitExceeded)
async def custom_rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """Custom rate limit exceeded response."""
    logger.warning(
        "Rate limit exceeded for %s on %s",
        get_remote_address(request),
        request.url.path
    )
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limit_exceeded",
            "message": "Too many requests. Please try again later.",
            "retry_after": exc.detail.split("Retry after ")[1] if "Retry after" in exc.detail else "60 seconds"
        },
        headers={"Retry-After": "60"}
    )
```

---

## 🟠 FIX #4: Path Traversal Protection

### Problem
File download endpoint vulnerable to path traversal via symlinks.

### Solution: Strict Path Validation

**Update `server.py:765-780`**
```python
import os

@app.get("/api/files/{file_path:path}")
@limiter.limit("100/minute")
async def get_file(
    request: Request,
    file_path: str,
    uid: int = Depends(get_uid)
):
    """Download a file from user's download directory."""
    
    # Input validation
    if not file_path or file_path.strip() == "":
        raise HTTPException(400, "File path cannot be empty")
    
    # Reject dangerous patterns
    dangerous_patterns = ['..', '~', '\x00', '\\\\', '//']
    if any(pattern in file_path for pattern in dangerous_patterns):
        logger.warning("Rejected dangerous file path: %s from uid=%s", file_path, uid)
        raise HTTPException(403, "Invalid file path")
    
    # Reject absolute paths
    if os.path.isabs(file_path):
        raise HTTPException(403, "Absolute paths not allowed")
    
    dl_dir = user_dir(uid) / "downloads"
    target = dl_dir / file_path
    
    # Security checks
    try:
        # Use strict=True to reject non-existent paths
        resolved_target = target.resolve(strict=True)
        resolved_dl_dir = dl_dir.resolve(strict=True)
        
        # Ensure target is within download directory
        resolved_target.relative_to(resolved_dl_dir)
    except (ValueError, FileNotFoundError, OSError) as e:
        logger.warning(
            "Path traversal attempt blocked: %s from uid=%s - %s",
            file_path, uid, e
        )
        raise HTTPException(403, "Access denied")
    
    # Check for symlinks
    if target.is_symlink():
        logger.warning("Symlink access blocked: %s from uid=%s", file_path, uid)
        raise HTTPException(403, "Symlinks not allowed")
    
    # Verify file exists and is a regular file
    if not target.exists():
        raise HTTPException(404, "File not found")
    
    if not target.is_file():
        raise HTTPException(403, "Not a file")
    
    # Check file size (prevent serving huge files)
    max_size = 2 * 1024 * 1024 * 1024  # 2GB
    if target.stat().st_size > max_size:
        raise HTTPException(413, "File too large")
    
    # Log access for auditing
    logger.info("File download: %s by uid=%s", file_path, uid)
    
    return FileResponse(
        target,
        media_type="application/octet-stream",
        filename=target.name
    )
```

---

## 🟠 FIX #5: Improved Exception Handling

### Problem
Bare `except Exception: pass` blocks hide critical errors.

### Solution: Structured Error Handling

**Create `error_handling.py`**
```python
"""
Centralized error handling utilities.
"""
import logging
import functools
from typing import Callable, Any

logger = logging.getLogger(__name__)

def log_exceptions(operation_name: str):
    """Decorator to log exceptions with context."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.exception(
                    "%s failed in %s: %s",
                    operation_name,
                    func.__name__,
                    e,
                    extra={
                        "operation": operation_name,
                        "function": func.__name__,
                        "args": str(args)[:100],  # Truncate for safety
                    }
                )
                raise
        
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.exception(
                    "%s failed in %s: %s",
                    operation_name,
                    func.__name__,
                    e,
                    extra={
                        "operation": operation_name,
                        "function": func.__name__,
                        "args": str(args)[:100],
                    }
                )
                raise
        
        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper
    return decorator

class ErrorContext:
    """Context manager for safe error handling."""
    
    def __init__(self, operation: str, reraise: bool = False):
        self.operation = operation
        self.reraise = reraise
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            logger.exception(
                "%s failed: %s",
                self.operation,
                exc_val,
                extra={"operation": self.operation}
            )
            return not self.reraise  # Suppress if reraise=False
        return True
```

**Usage examples:**
```python
# Replace this:
try:
    content = env_path.read_text(encoding="utf-8")
except Exception:
    pass

# With this:
with ErrorContext("Loading .env file"):
    content = env_path.read_text(encoding="utf-8")

# Or use decorator:
@log_exceptions("File cleanup")
async def cleanup_temp_files(uid: int):
    # ... cleanup logic ...
    pass
```

---

## 🔧 Deployment Checklist

Before deploying with these fixes:

- [ ] Generate new `COOKIE_ENCRYPTION_KEY`
- [ ] Revoke old bot token, generate new one
- [ ] Clear all existing cookies and re-authenticate
- [ ] Run cookie migration script
- [ ] Test encrypted cookie loading
- [ ] Verify rate limiting works
- [ ] Test path traversal protection
- [ ] Review all logs for errors
- [ ] Update documentation
- [ ] Notify users of security update

---

## 📝 Testing the Fixes

**Test Cookie Encryption:**
```python
# test_crypto.py
from crypto_utils import get_crypto
from pathlib import Path

def test_cookie_encryption():
    crypto = get_crypto()
    
    # Test data
    cookie_data = "test_cookie_value"
    test_path = Path("test_cookie.enc")
    
    # Encrypt and save
    crypto.save_encrypted_cookie(test_path, cookie_data)
    assert test_path.exists()
    
    # Load and decrypt
    loaded = crypto.load_encrypted_cookie(test_path)
    assert loaded == cookie_data
    
    # Cleanup
    test_path.unlink()
    print("✓ Cookie encryption test passed")

if __name__ == "__main__":
    test_cookie_encryption()
```

**Test Rate Limiting:**
```python
# test_rate_limit.py
import requests
import time

def test_rate_limiting():
    base_url = "http://localhost:8080"
    headers = {"Authorization": "Bearer test_token"}
    
    # Make 11 requests (limit is 10/minute)
    for i in range(11):
        response = requests.post(
            f"{base_url}/api/download",
            headers=headers,
            json={"media_type": "all"}
        )
        print(f"Request {i+1}: {response.status_code}")
        
        if response.status_code == 429:
            print("✓ Rate limiting working correctly")
            return
    
    print("✗ Rate limiting not working")

if __name__ == "__main__":
    test_rate_limiting()
```

**Test Path Traversal Protection:**
```python
# test_path_traversal.py
import requests

def test_path_traversal():
    base_url = "http://localhost:8080"
    headers = {"Authorization": "Bearer test_token"}
    
    # Test dangerous paths
    dangerous_paths = [
        "../../../etc/passwd",
        "..\\..\\..\\windows\\system32\\config\\sam",
        "/etc/passwd",
        "~/../../etc/passwd",
        "test/../../../etc/passwd",
    ]
    
    for path in dangerous_paths:
        response = requests.get(
            f"{base_url}/api/files/{path}",
            headers=headers
        )
        if response.status_code == 403:
            print(f"✓ Blocked: {path}")
        else:
            print(f"✗ VULNERABILITY: {path} returned {response.status_code}")

if __name__ == "__main__":
    test_path_traversal()
```

---

## 🚀 Deployment Steps

1. **Backup current system:**
   ```bash
   tar -czf cuhibot_backup_$(date +%Y%m%d).tar.gz data/ cookies/ .env
   ```

2. **Update code:**
   ```bash
   git pull origin main
   pip install -r requirements.txt
   ```

3. **Generate encryption key:**
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

4. **Update `.env` with new values**

5. **Migrate cookies:**
   ```bash
   python migrate_cookies.py
   ```

6. **Test in staging environment**

7. **Deploy to production**

8. **Monitor logs for errors**

---

**Implementation Priority:**
1. Fix #1 (Credentials) - IMMEDIATE
2. Fix #2 (Cookie Encryption) - 24 hours
3. Fix #3 (Rate Limiting) - 48 hours
4. Fix #4 (Path Traversal) - 48 hours
5. Fix #5 (Error Handling) - 1 week

**Last Updated:** May 23, 2026
