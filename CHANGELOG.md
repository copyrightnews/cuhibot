# Changelog

This is the history of every major fix and feature added to **Cuhi Bot**. We try to keep things clear and readable.

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
