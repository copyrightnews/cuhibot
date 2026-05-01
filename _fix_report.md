══════════════════════════════
BUG FIX REPORT — PASS 17 (HARDENING)
══════════════════════════════
TOTAL BUGS FOUND     : 5
  CRITICAL           : 1
  HIGH               : 2
  MODERATE           : 2
  MINOR              : 0

TOTAL BUGS FIXED     : 5
TOTAL BUGS REMAINING : 0

ROOT CAUSES:
  1. [CRITICAL] Non-atomic profile writes during `/add`, `/remove`, and `/import` could lead to data corruption if multiple events hit the same user ID simultaneously.
  2. [HIGH] Subprocess cleanup relied on `proc.kill()` which may fail to reap deep process trees (gallery-dl -> yt-dlp child), leading to memory leaks and port exhaustion.
  3. [HIGH] 50MB file size limit not checked before `sendMediaGroup`, causing batch failures when a single large video was present.
  4. [MODERATE] Download engine used blocking filesystem polling, which is inefficient and can cause IO deadlocks under high load.
  5. [MODERATE] `/link` command respected the download archive, preventing users from re-downloading a link if it was previously processed.

FILES CHANGED        : bot.py, CHANGELOG.md
COMPILE VERIFIED     : YES (Python 3.11 syntax check OK)
CHECKLIST SCANNED    : YES (Rule 6 Deep Dive Checklist)

REMAINING ISSUES:
  NONE — Production Hardened for v1.3.2
══════════════════════════════
