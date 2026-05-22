# Cuhibot Bug Tracker & Technical Debt

This document tracks all identified bugs, technical debt, and areas requiring improvement.

---

## 🐛 Active Bugs

### BUG-001: Exposed Credentials in Repository
- **Status:** 🔴 CRITICAL - OPEN
- **File:** `.env`
- **Reported:** 2026-05-23
- **Description:** Production credentials committed to repository
- **Impact:** Complete security compromise
- **Fix:** Revoke all tokens, use environment variables, add to .gitignore
- **Assigned:** URGENT

### BUG-002: Race Condition in File Locking
- **Status:** 🟠 HIGH - OPEN
- **Files:** `bot.py:237-275`, `server.py:115-153`
- **Reported:** 2026-05-23
- **Description:** Time-of-check to time-of-use race between stale lock detection and acquisition
- **Impact:** Data corruption, lost updates
- **Reproduction:**
  ```python
  # Process A checks lock age
  age = time.time() - lock_path.stat().st_mtime
  if age > 30:
      lock_path.unlink(missing_ok=True)
      # Process B can create lock here before Process A
      continue
  ```
- **Fix:** Use proper OS-level file locking (fcntl.flock)
- **Assigned:** TBD

### BUG-003: Memory Leak in Task Set
- **Status:** 🟠 HIGH - OPEN
- **File:** `bot.py:160`
- **Reported:** 2026-05-23
- **Description:** Completed asyncio tasks never removed from _TASKS set
- **Impact:** Unbounded memory growth in long-running instances
- **Fix:** Implement periodic cleanup of completed tasks
- **Assigned:** TBD

### BUG-004: Unsafe Exception Swallowing
- **Status:** 🟠 MEDIUM - OPEN
- **Files:** Multiple (bot.py, server.py, update_env.py)
- **Reported:** 2026-05-23
- **Description:** Bare `except Exception: pass` blocks hide critical errors
- **Impact:** Silent failures, difficult debugging
- **Locations:**
  - `bot.py:84` - .env parsing
  - `bot.py:693` - download state clearing
  - `server.py:832` - flag cleanup
  - `update_env.py:20` - tunnel log parsing
- **Fix:** Add logging to all exception handlers
- **Assigned:** TBD

### BUG-005: Potential Command Injection
- **Status:** 🟠 HIGH - OPEN
- **File:** `bot.py:990-1010`
- **Reported:** 2026-05-23
- **Description:** User URLs passed to subprocess without proper escaping
- **Impact:** Potential command injection via crafted URLs
- **Fix:** Use shlex.quote() for all user inputs
- **Assigned:** TBD

### BUG-006: Path Traversal Vulnerability
- **Status:** 🟠 HIGH - OPEN
- **File:** `server.py:765-780`
- **Reported:** 2026-05-23
- **Description:** File download endpoint uses strict=False in path resolution
- **Impact:** Potential unauthorized file access via symlinks
- **Fix:** Use strict=True and add symlink checks
- **Assigned:** TBD

### BUG-007: Missing Rate Limiting
- **Status:** 🟠 HIGH - OPEN
- **File:** `server.py` (all endpoints)
- **Reported:** 2026-05-23
- **Description:** No rate limiting on API endpoints
- **Impact:** DoS vulnerability, resource exhaustion
- **Fix:** Implement slowapi or fastapi-limiter
- **Assigned:** TBD

### BUG-008: Insecure Cookie Storage
- **Status:** 🔴 CRITICAL - OPEN
- **Files:** `bot.py:389-395`, `server.py:285-295`
- **Reported:** 2026-05-23
- **Description:** Cookies stored in plaintext
- **Impact:** Session hijacking if server compromised
- **Fix:** Encrypt cookies at rest using cryptography library
- **Assigned:** URGENT

### BUG-009: Weak Session Management
- **Status:** 🟠 MEDIUM - OPEN
- **File:** `bot.py:1803`
- **Reported:** 2026-05-23
- **Description:** 30-day session expiration too long, no rotation
- **Impact:** Increased attack window for stolen tokens
- **Fix:** Reduce to 7 days, implement refresh tokens
- **Assigned:** TBD

### BUG-010: CORS Misconfiguration
- **Status:** 🟠 MEDIUM - OPEN
- **File:** `server.py:698-720`
- **Reported:** 2026-05-23
- **Description:** allow_credentials=True with multiple localhost origins
- **Impact:** CSRF vulnerability
- **Fix:** Restrict CORS in production, disable credentials for localhost
- **Assigned:** TBD

### BUG-011: Missing Input Validation
- **Status:** 🟡 MEDIUM - OPEN
- **File:** `server.py:645-650`
- **Reported:** 2026-05-23
- **Description:** normalize_chat() accepts arbitrary large integers
- **Impact:** Telegram API errors, potential integer overflow
- **Fix:** Validate Telegram ID ranges
- **Assigned:** TBD

### BUG-012: No File Integrity Checks
- **Status:** 🟡 MEDIUM - OPEN
- **File:** `bot.py` (download functions)
- **Reported:** 2026-05-23
- **Description:** Downloaded files not verified for integrity
- **Impact:** Corrupted downloads, potential malware
- **Fix:** Add checksum verification, file type validation
- **Assigned:** TBD

### BUG-013: Inefficient File Discovery
- **Status:** 🟡 LOW - OPEN
- **File:** `bot.py:1324-1400`
- **Reported:** 2026-05-23
- **Description:** rglob("*") scans entire directory tree
- **Impact:** Slow performance with many files
- **Fix:** Use os.scandir() or incremental scanning
- **Assigned:** TBD

### BUG-014: Hardcoded File Size Limit
- **Status:** 🟡 LOW - OPEN
- **File:** `bot.py:143`
- **Reported:** 2026-05-23
- **Description:** 50MB limit doesn't account for Telegram Premium (2GB)
- **Impact:** Unnecessary file size restrictions
- **Fix:** Detect account type and adjust limits
- **Assigned:** TBD

### BUG-015: Incomplete Health Check
- **Status:** 🟡 LOW - OPEN
- **File:** `server.py:738`
- **Reported:** 2026-05-23
- **Description:** /healthz always returns OK without checking dependencies
- **Impact:** False positive health status
- **Fix:** Add checks for disk, database, Telegram API
- **Assigned:** TBD

---

## 🔧 Technical Debt

### DEBT-001: JSON Files Instead of Database
- **Priority:** HIGH
- **Description:** Using JSON files with file locks instead of proper database
- **Impact:** Data consistency issues, poor performance, no ACID guarantees
- **Effort:** Large (requires migration)
- **Recommendation:** Migrate to SQLite with proper transactions

### DEBT-002: Code Duplication
- **Priority:** MEDIUM
- **Description:** Env loading and locking code duplicated in bot.py and server.py
- **Impact:** Maintenance burden, inconsistency risk
- **Effort:** Small
- **Recommendation:** Extract to shared utils.py module

### DEBT-003: Missing Type Hints
- **Priority:** MEDIUM
- **Description:** Incomplete type annotations throughout codebase
- **Impact:** Reduced IDE support, harder to catch type errors
- **Effort:** Medium
- **Recommendation:** Add type hints, enable mypy strict mode

### DEBT-004: Insufficient Test Coverage
- **Priority:** HIGH
- **Description:** Only 8 test cases, no integration tests
- **Impact:** Regressions go undetected, difficult refactoring
- **Effort:** Large
- **Recommendation:** Increase coverage to >80%, add CI/CD

### DEBT-005: No Backup System
- **Priority:** HIGH
- **Description:** No automated backups of user data
- **Impact:** Data loss risk
- **Effort:** Medium
- **Recommendation:** Implement periodic cloud backups

### DEBT-006: Insufficient Logging
- **Priority:** MEDIUM
- **Description:** Many operations lack adequate logging
- **Impact:** Difficult debugging and auditing
- **Effort:** Medium
- **Recommendation:** Add structured logging, log aggregation

### DEBT-007: Inconsistent Error Messages
- **Priority:** LOW
- **Description:** Error messages lack consistency
- **Impact:** Poor user experience
- **Effort:** Small
- **Recommendation:** Create error message constants, add i18n

---

## 🎯 Feature Requests / Enhancements

### FEAT-001: Multi-Language Support
- **Priority:** LOW
- **Description:** Add internationalization (i18n)
- **Effort:** Medium

### FEAT-002: Advanced Scheduling
- **Priority:** MEDIUM
- **Description:** Support complex cron expressions, timezone handling
- **Effort:** Small

### FEAT-003: Webhook Support
- **Priority:** LOW
- **Description:** Add webhook notifications for download completion
- **Effort:** Small

### FEAT-004: Duplicate Detection
- **Priority:** MEDIUM
- **Description:** Implement perceptual hashing to detect duplicate media
- **Effort:** Medium

### FEAT-005: Bandwidth Throttling
- **Priority:** LOW
- **Description:** Add configurable download speed limits
- **Effort:** Small

---

## 📊 Bug Statistics

- **Total Bugs:** 15
- **Critical:** 2 (13%)
- **High:** 5 (33%)
- **Medium:** 5 (33%)
- **Low:** 3 (20%)

**Open:** 15 | **In Progress:** 0 | **Resolved:** 0

---

## 🔍 Known Limitations

1. **Windows-Specific Issues:**
   - File locking behavior differs from Unix
   - Path handling requires special care
   - Batch script dependencies

2. **Scalability Concerns:**
   - JSON file storage doesn't scale beyond ~1000 users
   - No horizontal scaling support
   - Single-threaded download processing

3. **Platform Support:**
   - Only tested on Windows
   - Docker support exists but not extensively tested
   - No macOS or Linux native support documented

4. **External Dependencies:**
   - Relies on gallery-dl which may break with platform changes
   - Cloudflare tunnel dependency for local development
   - No fallback if Telegram API is down

---

## 🚀 Recommended Fixes Priority

### Sprint 1 (Week 1) - Critical Security
1. BUG-001: Revoke exposed credentials
2. BUG-008: Encrypt cookie storage
3. BUG-007: Add rate limiting
4. BUG-006: Fix path traversal

### Sprint 2 (Week 2) - High Priority Bugs
5. BUG-002: Fix race condition
6. BUG-005: Prevent command injection
7. BUG-003: Fix memory leak
8. BUG-009: Improve session management

### Sprint 3 (Week 3) - Medium Priority
9. BUG-004: Improve exception handling
10. BUG-010: Fix CORS configuration
11. BUG-011: Add input validation
12. DEBT-001: Start database migration

### Sprint 4 (Week 4) - Testing & Quality
13. DEBT-004: Increase test coverage
14. DEBT-006: Improve logging
15. BUG-012: Add file integrity checks

---

## 📝 Notes

- This tracker should be updated as bugs are fixed
- Each bug should have a corresponding GitHub issue
- Security bugs should be handled privately until fixed
- Regular security audits recommended quarterly

**Last Updated:** 2026-05-23  
**Next Review:** 2026-06-23
