# Changelog

This is the history of every major fix and feature added to **Cuhi Bot**. We try to keep things clear and readable.

## [2.3.0] Comprehensive Bug Fix Resolution — 2026-05-22

### Features & Security Realignment
- **Ecosystem Upgrade v2.3.0**: Aligned the Telegram Bot server, companion Mini App, and native Capacitor Android client under unified version `2.3.0` (versionCode `4`).
- **Cryptographically Secure URL Source Deletion**: Migrated callback queries in Telegram from fragile index-based tracking to secure, race-free SHA-256 hash slices of target URLs.
- **Wipe Downloads Directory Resilience**: Wrapped all background directory cleanups within robust exception-safe boundaries to prevent filesystem failures from halting execution flows.
- **Asynchronous Execution Threading**: Restructured blocking I/O calls to use non-blocking `asyncio.to_thread` for `cookie_summary` analysis.
- **Closure Scope Late-Binding Fix**: Hardened dynamic bulk profile imports by properly capturing platform targets inside closures.
- **Mirror Layout Parity**: Ensured identical feature sync for XSS escape utilities, out-of-scope variable safety, and dynamic server-side session checks in all HTML companion files.

---

## [2.2.0] Release and Final Ecosystem Hardening — 2026-05-22

### Features & Security Realignment
- **Unified Release v2.2.0**: Aligned both the core Telegram Bot server and the standalone mobile Android app under the clean, synchronized release version `2.2.0`.
- **Harden Production Security**: Configured the bot to fail-closed by default on unconfigured user lists in production, and updated the recommended execution commands to enforce `-e PRODUCTION=1`.
- **Lightweight CI and Compilation Smoke Testing**: Added compilation validations (`py_compile`) and automated Docker image build verification steps directly to the GitHub Actions test workflow.
- **Access Control and Dev Documentation Alignment**: Fixed the user ignore documentation, corrected the UI mirror synchronisation guides in the quickstart instructions, and expanded guidelines for local developers in the contribution checklist.
- **Repository Hygiene Cleanup**: Purged large internal scaffolding logs and temporary fixing reports, leaving the branch pristine and focused purely on production code.

---

## [2.1.0] Security Hardening and Deep Audit Bug Fixes — 2026-05-21

### Security & Architecture Hardening
- **Secure Capacitor Auth**: Deprecated stateless HMAC token generation in favor of cryptographically secure random session tokens (`secrets.token_hex(32)`) prefixed with `cuhi_session_token_`, completely eliminating `BOT_TOKEN` leak vulnerabilities.
- **Tight CORS Restrictions**: Replaced wildcard `allow_origins=["*"]` with explicit allowed origins mapping localhost, `PUBLIC_DOMAIN`, and dynamic subdomains matching `https://*.github.io` to ensure robust origin isolation.
- **Advisory File Lock Enforcement**: Wrapped all file-based operations targeting settings, profiles, queues, and cookie writes inside cooperative, thread-safe `locked_file` blocks.
- **Static Assets completeness in Docker**: Copied `logo.jpg` and all static resources directly into the production Docker build container to prevent served static assets returning 404 errors.

### Local Tooling & Repo Hygiene
- **Restored Local Development Tooling**: Rebuilt and restored the Windows local quickstart runner `run_local.bat` and log parser `update_env.py` to automate Cloudflare Tunnel subdomain detection and `.env` parsing.
- **Clean Git Index**: Excluded and purged `node_modules` from Git history, standardizing dependencies to clean builds.
- **CI Workflows**: Configured high-coverage testing workflows (`test.yml`) running automated test suites on push and PR.

---

## [2.0.1] Deep Bug Scan Fixes — 2026-05-08

### Critical & Moderate Bug Fixes
- **Queue Runtime Crash**: Fixed `NameError` crash by properly defining `MINIAPP_QUEUE`.
- **Initialization Fix**: Correctly passed post_init functions during the bot builder sequence to prevent read-only property assignment errors and ensure the queue worker starts.
- **Escape Character Rendering**: Fixed Markdown formatting where literal backslashes appeared in the UI menu.
- **Async Safety**: Replaced blocking I/O calls (`_read_profiles_sync`, `_read_settings_sync`) within the download routines with proper executor-backed awaitable counterparts.
- **Exception Handling Safety**: Removed bare `except:` blocks that suppressed critical system signals (like KeyboardInterrupt) ensuring graceful shutdowns.
- **Missing Feedback**: Added a proper error message when a user tries to export an empty list of sources.
- **Server Safety**: Replaced unsafe `os.environ` index with a `.get()` fallback in `server.py` to prevent crashes when `BOT_TOKEN` is missing at load time.

---

## [2.0.0] Stable Final Release — 2026-05-04

### Async Architecture & Stability Patch
- **Full Async I/O**: Migrated all blocking disk operations (JSON reading/writing, directory scans) to a dedicated thread pool (`ThreadPoolExecutor`). This makes the bot 100% responsive even during massive multi-user downloads.
- **Resource Leak Fix**: Implemented `contextlib.ExitStack` in the media group sender to guarantee that every file descriptor is closed properly, preventing "Too many open files" crashes.
- **Throttled Status**: Refined the status update engine to prevent Telegram rate limits and ensure smooth UI transitions.
- **Atomic Locking**: Optimized file locking with monotonic timers for safer concurrent state management.

---

## [1.3.3] — 2026-05-01

### Grouping and Reliability Fix
- **Native Video Grouping**: Videos are now properly packaged into 10-item media groups alongside photos, significantly reducing chat clutter.
- **Interleaved Downloads**: The "Both" download option now intelligently mixes photos and videos together instead of performing two separate passes.
- **Anti-Rate Limit**: Increased safety delays and implemented `--continue` flags to prevent gallery-dl from dropping out mid-profile due to platform restrictions.

- **Static Analysis (Pass 18)**: Performed a deep-dive PEP-8 formatting pass using `flake8` to resolve all lingering indentation inconsistencies and ensure standard alignment.

---

## [1.3.2] — 2026-05-01

### The "Zero-Error" Update
We did a massive deep-dive (Pass 17) to make the bot truly production-ready.
- **Atomic Profiles**: No more data loss. We added OS-level file locking so profile changes are always safe.
- **Smart Uploads**: The bot now pre-checks file sizes. If a file is over 50MB (Telegram's limit), it skips it and tells you why instead of crashing the whole batch.
- **Bypass Archive**: You can now use `/link <url>` to re-download things even if they were already archived.
- **Real-time Fix**: The download engine is now much more responsive and handles process cleanup properly on Windows.

---

## [1.3.1] — 2026-05-01

### Stability Release
This was our 16th audit pass. We focused on cleaning up memory leaks and fixing minor UI glitches in the callback handlers.

---

## [1.3.0] — 2026-05-01

### Feature Release
We added 5 big features to make the bot more autonomous:
- **Instant Links**: Use `/link <url>` for one-off downloads.
- **Auto-Schedules**: The bot can now run on a timer (6h/12h/24h).
- **Auto-Cleanup**: It deletes temporary files automatically after sending.
- **Import/Export**: Move your sources between different bot instances easily.
- **Live Progress**: See exactly how many files are being sent in real-time.

---

## [1.2.x] and older
*For the full history of earlier versions, see the Git commit logs.*
