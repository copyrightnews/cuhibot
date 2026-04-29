# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in **Cuhi Bot**, please **do not open a public GitHub issue**.

Instead, report it privately via email:

| Maintainer | Email |
|------------|-------|
| ebnycuhie | ebnycuhie@gmail.com |
| sayfalse | nahidurrahmanx@gmail.com |

Please include:
- A clear description of the vulnerability
- Steps to reproduce it
- Potential impact
- Any suggested fix (optional)

We will acknowledge your report within **48 hours** and aim to release a fix within **7 days** for critical issues.

---

## Supported Versions

| Version | Supported |
|---------|-----------|
| `v1.1.x` (latest) | ✅ Yes |
| `< v1.1.0` | ❌ No |

---

## Security Features

Cuhi Bot implements the following security controls:

### 🔒 Access Control

| Feature | Details |
|---------|---------|
| **User allowlist** | Set `ALLOWED_USERS=id1,id2` to restrict the bot to specific Telegram user IDs. If unset, the bot runs in open mode (all users allowed). |
| **Admin system** | Set `ADMIN_IDS=id1,id2` to grant admin access. Admins can use `/admin` to view bot stats and always bypass the allowlist. |
| **Unauthorized logging** | All unauthorized access attempts are logged with user ID and username for auditing. |

### ⏱️ Rate Limiting

| Feature | Details |
|---------|---------|
| **Download rate limit** | Users must wait **30 seconds** between download requests (configurable via `RATE_LIMIT_SECONDS`). |
| **One active download** | Only one concurrent download per user is allowed. |

### 🛡️ Input Validation

| Feature | Details |
|---------|---------|
| **URL validation** | URLs must start with `https://`, belong to the correct platform domain, and be ≤ 500 characters. |
| **URL injection guard** | URLs containing `;`, `` ` ``, `|`, `$`, or `&&` are rejected to prevent shell injection. |
| **Max profiles limit** | Users can add at most **50 sources per platform** to prevent resource abuse. |
| **Cookie file size limit** | Uploaded cookie files must be ≤ **1 MB** to prevent disk abuse. |
| **Markdown escaping** | All user-supplied strings (usernames, names) are escaped before being rendered in Telegram messages. |

### 🗂️ Data Isolation

| Feature | Details |
|---------|---------|
| **Per-user directories** | All user data (profiles, settings, history, downloads, cookies) is stored in isolated directories keyed by Telegram user ID. |
| **Atomic file writes** | All JSON files are written with advisory file locks (`O_CREAT|O_EXCL`) to prevent race conditions and corruption. |
| **Stale lock cleanup** | Lock files older than 30 seconds are automatically removed to prevent deadlocks. |

### 🐳 Container Security

| Feature | Details |
|---------|---------|
| **Non-root user** | The Docker container runs as a dedicated non-root `botuser` to limit blast radius. |
| **Minimal base image** | Uses `python:3.11-slim` to reduce attack surface. |
| **No secrets in image** | All secrets (`BOT_TOKEN`, `COOKIE_*`) are passed as environment variables, never baked into the image. |

---

## Environment Variables — Security Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `BOT_TOKEN` | ✅ | Telegram Bot API token. **Never commit this to source control.** |
| `ALLOWED_USERS` | ❌ | Comma-separated Telegram user IDs allowed to use the bot. Leave empty for open access. |
| `ADMIN_IDS` | ❌ | Comma-separated Telegram user IDs with admin privileges (`/admin` command). |
| `COOKIE_INSTAGRAM` | ❌ | Netscape cookie text or base64 for Instagram. Stored on Railway volume. |
| `COOKIE_TIKTOK` | ❌ | Netscape cookie text or base64 for TikTok. |
| `COOKIE_FACEBOOK` | ❌ | Netscape cookie text or base64 for Facebook. |
| `COOKIE_X` | ❌ | Netscape cookie text or base64 for X/Twitter. |

> [!WARNING]
> **Never share your `BOT_TOKEN` or cookie files publicly.** If exposed, rotate the token immediately via [@BotFather](https://t.me/BotFather) and regenerate your cookies.

---

## Known Limitations

- Cookie files uploaded via the bot are stored on the Railway persistent volume and accessible only to that bot instance.
- The bot does not implement IP-based rate limiting (not needed for Telegram bots as Telegram enforces its own limits).
- File locking is advisory only — it protects against concurrent bot coroutines but not against external processes writing to the same files.

---

## Acknowledgements

Security research and responsible disclosure are valued and appreciated.
Thank you to everyone who helps keep Cuhi Bot secure. 🙏
