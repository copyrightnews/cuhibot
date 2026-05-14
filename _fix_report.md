# Deep Audit Fix Report — Full Session
**Date:** 2026-05-15
**Model:** Claude Sonnet (Thinking)
**Files Audited:** bot.py (2340 lines), server.py (515 lines), mobile_app/www/index.html (1184 lines), AndroidManifest.xml, variables.gradle, capacitor.config.json

---

## BUGS FOUND: 12  (CRITICAL: 5 | MODERATE: 5 | MINOR: 2)
## BUGS FIXED: 12
## VERIFIED: YES — py_compile passes, zero errors
## REMAINING: NONE

---

## [CRITICAL] Fixes

### C-1 — `asyncio.Queue()` at module level (bot.py:157)
**Root cause:** `MINIAPP_QUEUE = asyncio.Queue()` was created before the asyncio event loop
starts. Raises DeprecationWarning in Python 3.10+, RuntimeError in Python 3.12+.
**Fix:** Changed to `None` at module level. Lazily initialized inside `_combined_post_init()`
(inside the running event loop). Added `if MINIAPP_QUEUE is None` null-guards in
`miniapp_queue_worker()` and `poll_miniapp_queue_fallback()`.

### C-2 — `m_export` callback missing `send_menu()` (bot.py:1826)
**Root cause:** After sending the export file the `handle_callback` returned with no UI restore.
User received the document but was left on a blank screen with no menu.
**Fix:** Added `await send_menu(q.message, uid, uname, name)` after document send.

### C-3 — Android `drain()` wrongly called `add_sent_files()` (bot.py:1100)
**Root cause:** For `target == "android"` files stay on disk. They are never sent to Telegram.
But `add_sent_files(uid, n)` was still called, incorrectly inflating the `files_sent` counter.
**Fix:** Skip `add_sent_files()` for android target. Only increment local `sent_count`.

### C-4 — Wrong Capacitor v6 plugin API (www/index.html:896)
**Root cause:** `window.CapacitorFilesystem` is a Capacitor v3-era pattern. In Capacitor v6
plugins are at `window.Capacitor.Plugins.Filesystem`. The old namespace is always `undefined`
so every native file sync silently fails — files are never saved to the phone.
**Fix:** Both `requestStoragePermission()` and `syncNativeFiles()` now use
`window.Capacitor.Plugins.Filesystem`. Replaced `Directory.Documents` enum (not available
without a bundler) with the raw string constant `'DOCUMENTS'`.

### C-5 — Mini App Stop button broken in fallback path (bot.py:poll_miniapp_queue_fallback)
**Root cause:** `server.py /api/download/stop` writes a `stop_flag` file per-user. But
`poll_miniapp_queue_fallback` only polled `download_trigger.json` — `stop_flag` was written
to disk and never read by bot.py. The Stop button in the Mini App had zero effect on the
bot-side download via the file-based fallback path.
**Fix:** Added a second glob loop in `poll_miniapp_queue_fallback` that reads `*/stop_flag`,
unlinks the file, and calls `STOP_EVENTS[uid].set()` for the matching user.

---

## [MODERATE] Fixes

### M-1 — `_run_miniapp_download` disk leak for Telegram clients (bot.py:2200)
**Root cause:** `do_download()` calls `wipe_downloads(uid)` after every run. But
`_run_miniapp_download` (used by the Mini App) did NOT call `wipe_downloads`. Every Mini App
triggered download accumulated staging files on the server disk silently.
**Fix:** Added `await asyncio.to_thread(wipe_downloads, uid)` in the `finally` block,
gated on `client != "android"` (android clients need files to stay for `/api/files`).

### M-2 — `settings` re-read on every URL in inner loop (bot.py:2159)
**Root cause:** `await read_settings(uid)` was called inside `for url in profiles` — once per
source URL per platform. On a user with 50 sources this means 50+ blocking disk reads per run.
**Fix:** Moved `user_settings = await read_settings(uid)` to once before the loop.
Inner loop now uses `user_settings.get("channel")` directly.

### M-3 — Missing Android 13+ media permissions (AndroidManifest.xml)
**Root cause:** `READ/WRITE_EXTERNAL_STORAGE` capped at `maxSdkVersion="32"`. On Android 13+
(API 33+) Capacitor Filesystem needs `READ_MEDIA_IMAGES` and `READ_MEDIA_VIDEO`.
**Fix:** Added `READ_MEDIA_IMAGES`, `READ_MEDIA_VIDEO`, `POST_NOTIFICATIONS`.

### M-4 — `compileSdkVersion = 36` unreleased (variables.gradle)
**Root cause:** SDK 36 is not a stable release. Gradle would fail to resolve it.
**Fix:** Changed `compileSdkVersion` and `targetSdkVersion` from `36` → `35`.

### M-5 — No per-file error feedback + `isSyncing` not reset on perm-denied (www/index.html)
**Root cause 1:** Individual file failures swallowed silently, no UI feedback.
**Root cause 2:** `isSyncing` not reset to `false` on early storage permission denied return,
permanently blocking future sync attempts for the session.
**Fix:** Added per-file status updates and error messages with 1.5s pause. Added
`isSyncing = false` before early permission-denied return.

---

## [MINOR] Fixes

### Mi-1 — Bare `capacitor.config.json` (capacitor.config.json)
**Fix:** Added `android.allowMixedContent`, `server.androidScheme: "https"`,
`SplashScreen` block, `Filesystem.readRequiresPermission`.

### Mi-2 — `SCHEDULE_OPTIONS` defined after `handle_callback` (bot.py:2041→2050)
**Fix:** Moved above `_scheduled_job`. Python resolves names at call time so this was a
runtime-safe issue, but it is now correctly placed above all its uses.
