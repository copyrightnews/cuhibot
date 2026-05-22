# Bug Fix Audit Report

## e:\Copyright News\cuhibot\server.py Audit
[CRITICAL] Line 581 — root cause: Plaintext cookie storage and retrieval via file read_text/write_text.
[MODERATE] Line 118 — root cause: Advisory file locking TOCTOU race condition.
[MODERATE] Line 424 — root cause: CORS allowed_origins configuration enables credentials with multiple localhost variants.
[MODERATE] Line 785 — root cause: Target path resolution in get_file uses strict=False which permits traversal via symlinks.
[MODERATE] Line 623 — root cause: normalize_chat has no range validation on parsed Telegram IDs.
[MODERATE] Line 345 — root cause: Missing rate limiting on POST and resource-heavy endpoints.
[MINOR]    Line 481 — root cause: healthz check returns ok status without evaluating subsystem health.
[MINOR]    Line 59 — root cause: Bare exception swallowing on environment loading without logs.

## e:\Copyright News\cuhibot\bot.py Audit
[CRITICAL] Line 383 — root cause: Plaintext cookie resolution and file loading.
[MODERATE] Line 260 — root cause: TOCTOU race condition in advisory file locking.
[MODERATE] Line 990 — root cause: Passing raw user URL input without shlex.quote to subprocess.
[MODERATE] Line 247 — root cause: Fire-and-forget tasks in _TASKS set never cleared when done.
[MODERATE] Line 1803 — root cause: token expires_at is 30 days with no rotation.
[MINOR]    Line 251 — root cause: MINIAPP_QUEUE set to None with no validation before access.
[MINOR]    Line 1324 — root cause: Recursive rglob("*") is slow and resource-heavy for directory traversal.
[MINOR]    Line 212 — root cause: Hardcoded 50MB telegram file limit without account check.
[MINOR]    Line 84 — root cause: Bare except block hides env loading and startup errors.
