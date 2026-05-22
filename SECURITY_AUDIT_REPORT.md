# Cuhibot Security & Code Quality Audit Report
**Date:** May 23, 2026  
**Auditor:** Kiro AI Assistant  
**Scope:** Complete codebase analysis for bugs, security vulnerabilities, and code quality issues

---

## Executive Summary

This audit identified **23 issues** across security, reliability, and code quality categories:
- **5 Critical Security Issues** 🔴
- **8 High Priority Bugs** 🟠
- **6 Medium Priority Issues** 🟡
- **4 Low Priority/Code Quality** 🟢

---

## 🔴 CRITICAL SECURITY ISSUES

### 1. **Exposed Sensitive Credentials in .env File**
**File:** `.env`  
**Severity:** CRITICAL  
**Description:** The `.env` file contains hardcoded authentication tokens, cookies, and API keys that are committed to the repository.

**Evidence:**
```env
BOT_TOKEN="8786029213:AAH8h5uHuKr6Myw7qfGP2xk3CjS0aUm0w04"
ADMIN_IDS="7232714487"
COOKIE_FACEBOOK="[full cookie data exposed]"
COOKIE_INSTAGRAM="[full session tokens exposed]"
COOKIE_TIKTOK="[full authentication data exposed]"
COOKIE_X="[full auth tokens exposed]"
```

**Impact:**
- Anyone with repository access can impersonate the bot
- Social media accounts can be hijacked
- User privacy is compromised

**Recommendation:**
1. **IMMEDIATELY** revoke all exposed tokens and regenerate new ones
2. Add `.env` to `.gitignore` (if not already)
3. Use `.env.example` with placeholder values instead
4. Rotate all social media cookies
5. Consider using environment variable management tools (e.g., AWS Secrets Manager, HashiCorp Vault)

---

### 2. **Insecure Cookie File Storage**
**Files:** `cookies/` directory, `bot.py` (lines 389-395), `server.py` (lines 285-295)  
**Severity:** CRITICAL  
**Description:** Cookie files containing authentication sessions are stored in plaintext without encryption.

**Evidence:**
```python
def resolve_cookie(uid: int, platform: str) -> Path:
    user_cookie = cdir(uid) / cookie_name
    global_cookie = global_cookie_dir() / cookie_name
    if user_cookie.exists():
        return user_cookie  # Returns plaintext cookie file
```

**Impact:**
- If the server is compromised, all user social media sessions are exposed
- Cookies can be stolen and used to access user accounts

**Recommendation:**
1. Encrypt cookie files at rest using `cryptography` library
2. Use per-user encryption keys derived from secure secrets
3. Implement cookie expiration and rotation policies
4. Add integrity checks (HMAC) to detect tampering

---

### 3. **Missing Rate Limiting on API Endpoints**
**File:** `server.py` (all `/api/*` endpoints)  
**Severity:** HIGH  
**Description:** No rate limiting is implemented on FastAPI endpoints, allowing potential DoS attacks.

**Evidence:**
```python
@app.post("/api/download")
async def trigger_download(body: DownloadTrigger, uid: int = Depends(get_uid)):
    # No rate limiting check
    running_flag = user_dir(uid) / "download_running"
```

**Impact:**
- Attackers can spam download requests
- Server resources can be exhausted
- Legitimate users may be denied service

**Recommendation:**
1. Implement rate limiting using `slowapi` or `fastapi-limiter`
2. Add per-user request quotas (e.g., 10 requests/minute)
3. Implement exponential backoff for repeated failures
4. Log and alert on suspicious activity patterns

---

### 4. **Path Traversal Vulnerability in File Download**
**File:** `server.py` (lines 765-780)  
**Severity:** HIGH  
**Description:** While there is a security check, the implementation has a potential bypass.

**Evidence:**
```python
@app.get("/api/files/{file_path:path}")
async def get_file(file_path: str, uid: int = Depends(get_uid)):
    dl_dir = user_dir(uid) / "downloads"
    target = dl_dir / file_path
    
    # Security check exists but uses strict=False
    try:
        target.resolve(strict=False).relative_to(dl_dir.resolve(strict=False))
    except (ValueError, OSError):
        raise HTTPException(403, "Access denied")
```

**Issue:** Using `strict=False` allows resolution of non-existent paths, which could be exploited with symlinks.

**Recommendation:**
1. Use `strict=True` for path resolution
2. Validate file_path against a whitelist of allowed characters
3. Reject paths containing `..`, `~`, or absolute paths
4. Add additional checks for symlinks:
```python
if target.is_symlink():
    raise HTTPException(403, "Symlinks not allowed")
```

---

### 5. **Weak Session Token Generation**
**File:** `bot.py` (line 1803)  
**Severity:** MEDIUM-HIGH  
**Description:** Session tokens use `secrets.token_urlsafe(32)` which is good, but there's no token rotation or expiration enforcement.

**Evidence:**
```python
app_token = f"cuhi_session_token_{secrets.token_urlsafe(32)}"
session_data = {
    "id": uid,
    "first_name": name,
    "username": uname,
    "expires_at": time.time() + 86400 * 30,  # 30 days - too long!
}
```

**Issues:**
- 30-day expiration is excessive for a security-sensitive app
- No token refresh mechanism
- No session invalidation on logout

**Recommendation:**
1. Reduce token lifetime to 7 days maximum
2. Implement refresh tokens for long-lived sessions
3. Add `/api/logout` endpoint to invalidate tokens
4. Store token creation timestamp and enforce rotation

---

## 🟠 HIGH PRIORITY BUGS

### 6. **Race Condition in File Locking**
**File:** `bot.py` (lines 237-275), `server.py` (lines 115-153)  
**Severity:** HIGH  
**Description:** The file locking mechanism has a race condition between stale lock detection and lock acquisition.

**Evidence:**
```python
for attempt in range(max_retries):
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        break
    except FileExistsError:
        try:
            age = time.time() - lock_path.stat().st_mtime
            if age > 30:
                lock_path.unlink(missing_ok=True)
                continue  # Race condition: another process could create lock here
        except OSError:
            pass
```

**Impact:**
- Two processes could delete and recreate the lock simultaneously
- Data corruption in JSON files
- Lost updates to user settings

**Recommendation:**
1. Use `fcntl.flock()` on Unix or `msvcrt.locking()` on Windows for proper advisory locking
2. Consider using a proper database (SQLite) instead of JSON files with locks
3. Add lock acquisition logging for debugging

---

### 7. **Unhandled asyncio.Queue Creation Before Event Loop**
**File:** `bot.py` (line 159)  
**Severity:** HIGH  
**Description:** The code has a comment indicating this was fixed, but the fix is incomplete.

**Evidence:**
```python
# [FIXED] asyncio.Queue must NOT be created at module level before the event loop
# starts (raises DeprecationWarning in Py3.10+, RuntimeError in Py3.12+).
# It is created lazily inside _combined_post_init() instead.
MINIAPP_QUEUE: asyncio.Queue | None = None
```

**Issue:** While the variable is initialized to `None`, there's no guarantee that `_combined_post_init()` is called before the queue is accessed.

**Recommendation:**
1. Add explicit initialization check in all queue access points:
```python
if MINIAPP_QUEUE is None:
    raise RuntimeError("MINIAPP_QUEUE not initialized - call _combined_post_init() first")
```
2. Use a factory function instead of global variable
3. Add unit tests to verify initialization order

---

### 8. **Memory Leak in Task Management**
**File:** `bot.py` (line 160)  
**Severity:** MEDIUM-HIGH  
**Description:** Fire-and-forget tasks are stored in a set to prevent GC, but never cleaned up.

**Evidence:**
```python
_TASKS: set[asyncio.Task] = set()  # prevent GC of fire-and-forget tasks
```

**Impact:**
- Long-running bot instances will accumulate completed tasks
- Memory usage grows unbounded
- Potential OOM crashes on resource-constrained systems

**Recommendation:**
1. Implement task cleanup:
```python
def cleanup_completed_tasks():
    global _TASKS
    _TASKS = {task for task in _TASKS if not task.done()}
```
2. Call cleanup periodically (e.g., every hour)
3. Add task monitoring and alerting

---

### 9. **Unsafe Exception Handling Hides Errors**
**Files:** Multiple locations (bot.py, server.py, update_env.py)  
**Severity:** MEDIUM  
**Description:** Bare `except Exception:` blocks with `pass` statements hide critical errors.

**Evidence:**
```python
# bot.py line 84
except Exception:
    pass

# bot.py line 693
except Exception:
    pass

# server.py line 832
except Exception:
    pass
```

**Impact:**
- Silent failures make debugging impossible
- Data corruption may go unnoticed
- System state becomes inconsistent

**Recommendation:**
1. Always log exceptions:
```python
except Exception as e:
    logger.exception("Failed to process X: %s", e)
```
2. Use specific exception types where possible
3. Add error metrics/monitoring

---

### 10. **Potential Command Injection in gallery-dl Execution**
**File:** `bot.py` (lines 990-1010)  
**Severity:** HIGH  
**Description:** User-provided URLs are passed to subprocess without sufficient validation.

**Evidence:**
```python
def build_gallery_dl_cmd(url: str, out_dir: Path, ...) -> list[str]:
    cmd = ["gallery-dl", "--dest", str(out_dir)]
    # ... more args ...
    cmd.append(url)  # User input directly in command
```

**Issue:** While the URL is validated with regex, special characters in URLs could potentially be exploited.

**Recommendation:**
1. Use `shlex.quote()` for all user inputs:
```python
import shlex
cmd.append(shlex.quote(url))
```
2. Enforce stricter URL validation
3. Run gallery-dl in a sandboxed environment (Docker, firejail)

---

### 11. **Missing Input Validation on Channel IDs**
**File:** `server.py` (lines 645-650)  
**Severity:** MEDIUM  
**Description:** The `normalize_chat()` function doesn't validate input ranges.

**Evidence:**
```python
def normalize_chat(value) -> int | str:
    if isinstance(value, int):
        return value  # No range validation
    v = str(value).strip()
    if v.lstrip("-").isdigit():
        n = int(v)
        if n > 5000000000:
            return n  # Arbitrary large numbers accepted
```

**Impact:**
- Invalid channel IDs could cause Telegram API errors
- Potential integer overflow issues
- DoS through malformed requests

**Recommendation:**
1. Validate Telegram ID ranges (valid range: -1000000000000 to 1000000000000)
2. Reject obviously invalid values
3. Add error handling for Telegram API responses

---

### 12. **Insecure CORS Configuration**
**File:** `server.py` (lines 698-720)  
**Severity:** MEDIUM  
**Description:** CORS allows credentials from multiple origins including localhost.

**Evidence:**
```python
allowed_origins = [
    "http://localhost",
    "https://localhost",
    "capacitor://localhost",
    "http://localhost:5173",
    # ... many more localhost ports
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,  # Dangerous with multiple origins
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**Impact:**
- Potential CSRF attacks from malicious localhost apps
- Session hijacking if user visits malicious site
- Credential leakage

**Recommendation:**
1. In production, only allow the actual deployment domain
2. Disable `allow_credentials` for localhost origins
3. Use environment-specific CORS configs
4. Implement CSRF tokens for state-changing operations

---

### 13. **No Integrity Checks on Downloaded Files**
**File:** `bot.py` (download functions)  
**Severity:** MEDIUM  
**Description:** Downloaded files are not verified for integrity or malware.

**Impact:**
- Malicious files could be downloaded and forwarded
- Corrupted downloads go undetected
- Potential malware distribution

**Recommendation:**
1. Calculate and verify file checksums
2. Implement file type validation (magic bytes)
3. Add virus scanning integration (ClamAV)
4. Limit file sizes to prevent storage exhaustion

---

## 🟡 MEDIUM PRIORITY ISSUES

### 14. **Hardcoded Telegram File Size Limit**
**File:** `bot.py` (line 143)  
**Severity:** LOW-MEDIUM  
**Description:** The 50MB limit is hardcoded but Telegram's actual limit varies by account type.

**Evidence:**
```python
TELEGRAM_FILE_LIMIT = 50 * 1024 * 1024  # 50 MB Telegram Bot API cap
```

**Issue:** Premium Telegram accounts support 2GB files, but the bot doesn't detect this.

**Recommendation:**
1. Detect user account type via Telegram API
2. Adjust limits dynamically
3. Add configuration option for custom limits

---

### 15. **Inefficient File Discovery Algorithm**
**File:** `bot.py` (lines 1324-1400)  
**Severity:** MEDIUM  
**Description:** The file discovery uses `rglob("*")` which is slow for large directories.

**Evidence:**
```python
if out_dir.exists():
    for f in out_dir.rglob("*"):  # Scans entire tree
        if f.is_file():
            try:
                seen.add(f.resolve().absolute())
```

**Impact:**
- Slow performance with many files
- High CPU usage
- Delayed user feedback

**Recommendation:**
1. Use `os.scandir()` for better performance
2. Implement incremental scanning
3. Cache file listings
4. Add progress indicators

---

### 16. **Missing Database Transactions**
**File:** `bot.py`, `server.py` (all JSON file operations)  
**Severity:** MEDIUM  
**Description:** Using JSON files instead of a database leads to data consistency issues.

**Impact:**
- Concurrent writes can corrupt data
- No ACID guarantees
- Difficult to implement complex queries

**Recommendation:**
1. Migrate to SQLite for structured data
2. Use proper transactions
3. Implement database migrations
4. Keep JSON only for configuration

---

### 17. **No Backup or Recovery Mechanism**
**File:** Entire codebase  
**Severity:** MEDIUM  
**Description:** No automated backups of user data, settings, or history.

**Impact:**
- Data loss on disk failure
- No disaster recovery plan
- User frustration

**Recommendation:**
1. Implement periodic backups to cloud storage
2. Add export/import functionality
3. Document manual backup procedures
4. Test recovery procedures

---

### 18. **Insufficient Logging**
**Files:** Multiple  
**Severity:** LOW-MEDIUM  
**Description:** Many operations lack adequate logging for debugging and auditing.

**Recommendation:**
1. Add structured logging (JSON format)
2. Log all authentication attempts
3. Log all file operations
4. Implement log rotation
5. Add log aggregation (ELK stack, Grafana Loki)

---

### 19. **Missing Health Checks**
**File:** `server.py` (line 738)  
**Severity:** LOW-MEDIUM  
**Description:** The `/healthz` endpoint is too simple.

**Evidence:**
```python
@app.get("/healthz")
async def healthz():
    return {"status": "ok"}  # Always returns OK
```

**Recommendation:**
1. Check database connectivity
2. Verify disk space
3. Test external dependencies (Telegram API)
4. Return detailed status information

---

## 🟢 LOW PRIORITY / CODE QUALITY

### 20. **Inconsistent Error Messages**
**Files:** Multiple  
**Severity:** LOW  
**Description:** Error messages lack consistency and user-friendliness.

**Recommendation:**
1. Create error message constants
2. Use i18n for multi-language support
3. Provide actionable error messages

---

### 21. **Missing Type Hints**
**Files:** Multiple functions  
**Severity:** LOW  
**Description:** Some functions lack complete type annotations.

**Recommendation:**
1. Add type hints to all functions
2. Use `mypy` for static type checking
3. Enable strict mode in mypy config

---

### 22. **Code Duplication**
**Files:** `bot.py` and `server.py` (env loading, locking)  
**Severity:** LOW  
**Description:** Identical code blocks are duplicated across files.

**Recommendation:**
1. Extract common utilities to `utils.py`
2. Create shared modules for locking, env loading
3. Follow DRY principle

---

### 23. **Missing Unit Test Coverage**
**File:** `test_bot.py`  
**Severity:** LOW  
**Description:** Test coverage is minimal (only 8 test cases).

**Recommendation:**
1. Increase test coverage to >80%
2. Add integration tests
3. Implement CI/CD with automated testing
4. Add property-based testing (Hypothesis)

---

## Immediate Action Items (Priority Order)

1. **🔴 CRITICAL:** Revoke and rotate all exposed credentials in `.env`
2. **🔴 CRITICAL:** Implement cookie encryption at rest
3. **🟠 HIGH:** Fix race condition in file locking
4. **🟠 HIGH:** Add rate limiting to API endpoints
5. **🟠 HIGH:** Fix path traversal vulnerability
6. **🟠 HIGH:** Improve exception handling and logging
7. **🟡 MEDIUM:** Migrate from JSON files to SQLite
8. **🟡 MEDIUM:** Implement backup system
9. **🟢 LOW:** Increase test coverage
10. **🟢 LOW:** Add comprehensive documentation

---

## Security Best Practices Recommendations

1. **Implement Security Headers:**
   - Add `X-Content-Type-Options: nosniff`
   - Add `X-Frame-Options: DENY`
   - Add `Content-Security-Policy`
   - Add `Strict-Transport-Security`

2. **Enable Security Monitoring:**
   - Integrate with SIEM system
   - Set up intrusion detection
   - Monitor for suspicious patterns
   - Alert on security events

3. **Regular Security Audits:**
   - Schedule quarterly code reviews
   - Run automated security scanners (Bandit, Safety)
   - Perform penetration testing
   - Keep dependencies updated

4. **Implement Principle of Least Privilege:**
   - Run bot with minimal permissions
   - Use separate service accounts
   - Restrict file system access
   - Limit network access

---

## Conclusion

The Cuhibot codebase is functional but has significant security and reliability issues that need immediate attention. The most critical issues involve exposed credentials and insecure data storage. Addressing the high-priority items will significantly improve the security posture and reliability of the application.

**Overall Risk Rating:** HIGH ⚠️

**Recommended Timeline:**
- Critical issues: Fix within 24-48 hours
- High priority: Fix within 1 week
- Medium priority: Fix within 1 month
- Low priority: Address in next major release

---

**Report Generated:** May 23, 2026  
**Next Audit Recommended:** After critical fixes are implemented
