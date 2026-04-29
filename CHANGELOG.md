# Changelog

All notable changes to **Cuhi Bot** are documented here.  
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [1.2.0] — 2026-04-29

### Summary
Security hardening release. Added multi-layer access controls, rate limiting, and input
validation to prevent resource abuse. Added a formal `SECURITY.md` vulnerability reporting policy.

### Added

| # | Feature | Details |
|---|---------|---------|
| 1 | **`ALLOWED_USERS` allowlist** | Set a comma-separated list of Telegram user IDs; all others are silently rejected at every handler |
| 2 | **Per-user download rate limit** | Max 3 download requests per 60-second window per user; excess requests receive a clear cooldown message |
| 3 | **Max 50 profiles per platform** | Prevents unlimited source accumulation that would exhaust memory and disk |
| 4 | **1 MB cookie file size limit** | Rejects oversized cookie uploads before they are written to disk |
| 5 | **500-character URL length cap** | Blocks abnormally long URLs that could cause regex or parser issues |
| 6 | **`SECURITY.md`** | Professional vulnerability reporting policy with maintainer contacts and env-var security guide |

---

## [1.1.0] — 2026-04-29

### Summary
Five consecutive deep-audit passes resolved **31 bugs** across every module of the bot.  
The codebase is now production-ready with zero known logic errors, race conditions,
silent exceptions, or fake code paths.

---

### Fixed — Audit Pass 5

| # | Location | Bug | Fix |
|---|----------|-----|-----|
| 31 | `locked_file` | Stale-lock `continue` branch on the final retry attempt bypassed the `TimeoutError` raise, causing `yield` to execute with `fd = None` — no lock held | Added `if fd is None: raise TimeoutError(...)` guard after the retry loop |
| 30 | `handle_from_url` | URLs with query strings (e.g. `?hl=en`) produced handles like `user?hl=en`, corrupting archive directory names and status displays | Strip `?query` and `#fragment` before splitting on `/` |
| 29 | `do_download` | Loop unpacked unused `cookie_name` variable from `PLATFORMS.items()` | Changed to `(_, _, sleep)` |

---

### Fixed — Audit Pass 4

| # | Location | Bug | Fix |
|---|----------|-----|-----|
| 28 | `_send_group` | Retrying with `InputMediaPhoto(fh)` after first attempt sent 0 bytes — file handles were at EOF position | Removed retry from `_send_group`; `flush()` fallback to `_send_one()` handles retries by reopening files |
| 27 | All file I/O | `read_text()`/`write_text()` used system default encoding — `cp1252` on Windows corrupts non-ASCII usernames and URLs | Added `encoding="utf-8"` to every `read_text()` and `write_text()` call |
| 26 | `start_download_task` | `asyncio.ensure_future()` deprecated in Python 3.10+ | Replaced with `asyncio.create_task()` |
| 25 | `do_special_download` | On 0 files downloaded, showed `✅ Done! 0 file(s) sent` — misleading | Now shows correct 3-state message: no media found / stopped / done |
| 24 | `m_run` handler | Showed media-type picker even with no sources, then failed only after user picked a type | Added source check before displaying the picker |

---

### Fixed — Audit Pass 3

| # | Location | Bug | Fix |
|---|----------|-----|-----|
| 23 | `Status.set` | After sleeping `RetryAfter`, `last_at` was not updated — next call fired immediately, re-triggering rate limit | Update `last_at = time.monotonic()` after the sleep |
| 22 | `flush` | Document fallback used kind string `"doc"` which is not a branch in `_send_one` | Changed to `"document"` |
| 21 | `realtime_download` finally | Called `proc.communicate()` after process was already killed — deadlocks on closed pipe | Split into `proc.wait()` then separate `proc.stderr.read()` with individual timeouts |
| 20 | `do_download` | When 0 files downloaded (not stopped), showed `✅ Done! 0 file(s)` | Now shows `ℹ️ No new media found` |
| 19 | `dl_` callback | No guard for invalid choice values (`dl_5`, `dl_abc`, etc.) | Added `if choice not in ("1","2","3","4"): return` |

---

### Fixed — Audit Pass 2

| # | Location | Bug | Fix |
|---|----------|-----|-----|
| 18 | `bootstrap_env_cookies` | `base64.b64decode()` silently returned garbage bytes for short non-base64 strings — wrote corrupt cookie files | Added `validate=True` + Netscape heuristic check (`\t` or `#`) before accepting decoded content |
| 17 | `realtime_download` | `proc.communicate()` in `finally` blocked the event loop on large outputs | Replaced with async `proc.stderr.read()` wrapped in `asyncio.wait_for` |
| 16 | `write_profiles` | Empty profile list wrote a blank line — parser read it as a real (empty) URL entry | Truncate to empty string `""` when list is empty |
| 15 | `_send_one` | `TimedOut` exceptions during large uploads were swallowed — file never sent | Added 4-tier exponential backoff (5s, 10s, 20s, 40s) |
| 14 | `_send_group` | `RetryAfter` in media groups silently dropped entire batch | Added 3-tier backoff for `RetryAfter` |
| 13 | `logging` | No `logging.basicConfig` — all errors including `gallery-dl` stderr were invisible | Added structured logging with `%(asctime)s [%(levelname)s]` format |

---

### Fixed — Audit Pass 1

| # | Location | Bug | Fix |
|---|----------|-----|-----|
| 12 | `locked_file` | Used open()+write() for lock creation — not atomic; two coroutines could acquire simultaneously | Replaced with `os.open(O_CREAT|O_EXCL|O_WRONLY)` — OS-guaranteed atomic |
| 11 | `normalize_chat` | Called twice on channel IDs: once at save, once at use — turned `-1001234` into `-100-1001234` | Normalize only at save time; store normalised value; never re-normalize |
| 10 | `realtime_download` | Polling loop used `proc.wait()` blocking the event loop — bot became unresponsive during downloads | Replaced with `asyncio.wait_for(proc.wait(), timeout=0.5)` non-blocking poll |
| 9 | `archive_path` | Archive file stored inside the volatile download directory — wiped after each run, causing full re-downloads | Moved archive to persistent `$DATA_ROOT/<uid>/archives/<platform>/<handle>/` |
| 8 | `_release` | Removed `STOP_EVENTS[uid]` unconditionally — a new download's stop event was deleted if the old one finished | Added identity check: only remove if `STOP_EVENTS[uid] is ev` |
| 7 | `flush` | File handles opened for media group left open if `_send_group` raised — caused `PermissionError` on Windows | Wrapped in `try/finally`; all handles closed before falling back to `_send_one` |
| 6 | `Status.set` | `edit_text` called on every file discovered — hit Telegram rate limits immediately | Added `STATUS_MIN_GAP = 2.0s` throttle + `force=True` for final messages |
| 5 | `build_gdl_cmd` | `"both"` mode was passed to `gallery-dl` directly — gallery-dl has no "both" filter | Orchestrator now splits into two sequential calls: `photos` then `videos` |
| 4 | `highlights_url_for` | Generated `/stories/highlights/{user}/` — gallery-dl uses `/{user}/highlights/` | Fixed to `https://www.instagram.com/{handle}/highlights/` |
| 3 | `validate_url` | No domain check — users could add `https://evil.com/instagram.com/` as an Instagram source | Added domain allowlist check per platform |
| 2 | `sent_count` | Incremented before files were actually sent — history recorded wrong counts | Increment after `flush()` returns |
| 1 | `handle_from_url` | (early version) did not strip leading `@` from handles in some paths | Added `.lstrip("@")` |

---

## [1.0.0] — 2026-04-28

### Added
- Initial release of Cuhi Bot
- Support for Instagram, TikTok, Facebook, X/Twitter via `gallery-dl`
- Inline Telegram menu with full source management
- Per-user cookie upload support
- Channel forwarding
- Railway + Docker deployment
