<p align="center">
  <img src="https://img.shields.io/badge/Cuhi_Bot-Social_Media_Downloader-blueviolet?style=for-the-badge&logo=telegram&logoColor=white" alt="Cuhi Bot" />
</p>

<p align="center">
  <strong>A production-hardened, self-hosted Telegram bot for downloading and forwarding media from Instagram, TikTok, Facebook, and X (Twitter).</strong>
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#deployment">Deployment</a> •
  <a href="#environment-variables">Configuration</a> •
  <a href="#security">Security</a> •
  <a href="#commands">Commands</a> •
  <a href="CHANGELOG.md">Changelog</a> •
  <a href="#authors">Authors</a> •
  <a href="#license">License</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-1.3.1-brightgreen?style=flat-square" alt="Version" />
  <img src="https://img.shields.io/badge/python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/telegram--bot-22.1-26A5E4?style=flat-square&logo=telegram&logoColor=white" alt="python-telegram-bot" />
  <img src="https://img.shields.io/badge/gallery--dl-1.32-orange?style=flat-square" alt="gallery-dl" />
  <img src="https://img.shields.io/badge/yt--dlp-latest-red?style=flat-square" alt="yt-dlp" />
  <img src="https://img.shields.io/badge/deploy-Railway-0B0D0E?style=flat-square&logo=railway&logoColor=white" alt="Railway" />
  <img src="https://img.shields.io/badge/bugs_fixed-73-critical?style=flat-square" alt="Bugs Fixed" />
  <img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="License" />
</p>

<p align="center">
  <a href="https://t.me/copyrightnews">
    <img src="https://img.shields.io/badge/Telegram_Channel-@copyrightnews-26A5E4?style=for-the-badge&logo=telegram&logoColor=white" alt="Telegram Channel" />
  </a>
</p>

---

## Features

| Feature | Description |
|---------|-------------|
| 📸 **Multi-platform** | Download from **Instagram**, **TikTok**, **Facebook**, and **X/Twitter** |
| 🎬 **Media types** | Photos, videos, stories, highlights — or all at once |
| 📡 **Channel forwarding** | Auto-forward downloaded media to any Telegram channel or group |
| 🍪 **Cookie support** | Per-user cookie uploads + global env-var cookies for authenticated downloads |
| 📦 **Real-time streaming** | Media is sent to Telegram as it downloads — no waiting for full completion |
| 🗂️ **Download archive** | Persistent dedup archive prevents re-downloading the same content |
| 📜 **History tracking** | Full download history with timestamps, platforms, and file counts |
| 🚫 **Stop control** | Gracefully stop any running download mid-stream |
| 🗑️ **Disk management** | One-tap cleanup of cached downloads to free disk space |
| 🔒 **Access control** | User allowlist, admin system, rate limiting, and input validation |
| 🛡️ **Production-hardened** | 73 bugs fixed across 16 audit passes — zero-error codebase |

## Architecture

```
bot.py                  # Single-file bot (all logic)
├── Telegram Handlers   # /start, /cleanup, /admin, inline buttons, text input
├── Orchestrators       # do_download(), do_special_download()
├── Download Engine     # realtime_download() → gallery-dl + yt-dlp subprocess
├── Sender              # flush() → Telegram media groups / singles
│   ├── Size guard      # 50 MB Telegram API limit enforcement
│   ├── Retry logic     # Exponential backoff for TimedOut / RetryAfter
│   └── Smart cleanup   # Only deletes successfully-sent files
├── Persistence         # JSON-based settings, history, profiles
└── Utilities           # File locking, cookie resolution, validators
```

### Data Layout (on disk)

```
$DATA_ROOT/
└── <user_id>/
    ├── instagram_profiles.txt
    ├── tiktok_profiles.txt
    ├── facebook_profiles.txt
    ├── x_profiles.txt
    ├── settings.json
    ├── history.json
    ├── archives/           # Persistent download-dedup archives
    │   └── <platform>/<handle>/<mode>.txt
    └── downloads/          # Volatile — cleaned after each run

$COOKIES_ROOT/
├── _global/                # Env-var cookies (shared across all users)
│   ├── instagram.com_cookies.txt
│   ├── tiktok.com_cookies.txt
│   ├── facebook.com_cookies.txt
│   └── x.com_cookies.txt
└── <user_id>/              # Per-user uploaded cookies
    └── *.txt
```

## Quick Start

### Prerequisites

- Python 3.11+
- [gallery-dl](https://github.com/mikf/gallery-dl) installed and on PATH
- A Telegram Bot Token from [@BotFather](https://t.me/BotFather)

### Local Setup

```bash
# Clone the repo
git clone https://github.com/copyrightnews/cuhibot.git
cd cuhibot

# Install dependencies
pip install -r requirements.txt

# Set your bot token
export BOT_TOKEN="your-telegram-bot-token"

# (Optional) Restrict access to specific users
export ALLOWED_USERS="123456789,987654321"
export ADMIN_IDS="123456789"

# Run
python bot.py
```

## Deployment

### Railway (Recommended)

1. **Fork** this repo or connect it directly to [Railway](https://railway.app)
2. **Add a volume** (e.g. `cuhi-volume`) mounted at `/app/data`
3. **Set environment variables** (see below)
4. **Deploy** — Railway auto-builds from the Dockerfile

> [!TIP]
> The included `Dockerfile` handles everything: Python 3.11, ffmpeg, yt-dlp, pip dependencies, and gallery-dl.

### Docker (Self-hosted)

```bash
docker build -t cuhi-bot .
docker run -d \
  --name cuhi \
  -e BOT_TOKEN="your-token" \
  -e ALLOWED_USERS="123456789" \
  -e ADMIN_IDS="123456789" \
  -e COOKIE_INSTAGRAM="your-cookie-text-or-base64" \
  -v cuhi-data:/app/data \
  cuhi-bot
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BOT_TOKEN` | ✅ | — | Telegram Bot API token from @BotFather |
| `DATA_ROOT` | ❌ | `./data` | Path for user data, profiles, history, archives |
| `COOKIES_ROOT` | ❌ | `./cookies` | Path for cookie files |
| `ALLOWED_USERS` | ❌ | — | Comma-separated Telegram user IDs allowed to use the bot. If empty, all users are allowed |
| `ADMIN_IDS` | ❌ | — | Comma-separated admin user IDs. Admins always bypass allowlist and can use `/admin` |
| `COOKIE_INSTAGRAM` | ❌ | — | Netscape cookie text or base64 for Instagram |
| `COOKIE_TIKTOK` | ❌ | — | Netscape cookie text or base64 for TikTok |
| `COOKIE_FACEBOOK` | ❌ | — | Netscape cookie text or base64 for Facebook |
| `COOKIE_X` | ❌ | — | Netscape cookie text or base64 for X/Twitter |

> [!NOTE]
> Cookie values can be either **raw Netscape cookie text** or **base64-encoded** cookie text. The bot auto-detects the format at startup.

### Railway Volume Setup

For persistent storage on Railway, set:
```
DATA_ROOT=/app/data/storage
COOKIES_ROOT=/app/data/cookies
```
And attach a volume mounted at `/app/data`.

## Security

Cuhi Bot is production-hardened with multiple layers of security. See [SECURITY.md](SECURITY.md) for the full policy and vulnerability reporting.

| Layer | Protection |
|-------|------------|
| 🔒 **Access control** | User allowlist (`ALLOWED_USERS`) + admin system (`ADMIN_IDS`) |
| ⏱️ **Rate limiting** | 30-second cooldown between downloads per user |
| 🚫 **Concurrency guard** | Only one active download per user at a time |
| 🛡️ **Input validation** | Strict URL domain parsing, length, shell injection, and newline checks |
| 📏 **File size limits** | 50 MB Telegram API limit enforced pre-upload; 1 MB cookie file limit |
| 🔐 **Data isolation** | Per-user directories for all data — no cross-user access |
| 🔗 **Atomic file locking** | `O_CREAT|O_EXCL` locks prevent race conditions on concurrent writes |
| 📊 **Audit logging** | Unauthorized access attempts logged with user ID and username |
| 🐳 **Minimal container** | `python:3.11-slim` base with no baked-in secrets |

> [!WARNING]
> **Never commit your `BOT_TOKEN` or cookie files to source control.** Always use environment variables.

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Open the main menu |
| `/menu` | Same as /start |
| `/cleanup` | Free disk space by deleting cached downloads |
| `/admin` | Admin panel — view bot stats and active users (admin-only) |

### Inline Menu Actions

| Button | Action |
|--------|--------|
| ➕ Add source | Add a profile URL to track |
| 🚫 Remove source | Remove a saved profile |
| 🌐 My sources | List all saved profiles |
| ✅ Run download | Start downloading (photos/videos/both/files) |
| 📖 Stories | Download stories from a profile |
| ✨ Highlights | Download highlights from a profile |
| 🚫 Stop download | Gracefully cancel a running download |
| 📜 History | View recent download history |
| 🍪 Set cookies | Upload platform cookie files |
| 📊 Status | View current bot status |
| 📡 Set channel | Set output channel/group for downloads |
| 🗑️ Free disk | Delete cached files |

### Uploading Cookies

Send a `.txt` file named after the platform directly in the bot chat:

- `instagram.com_cookies.txt`
- `tiktok.com_cookies.txt`
- `facebook.com_cookies.txt`
- `x.com_cookies.txt`

The bot auto-detects and saves cookies for the correct platform.

## Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11 |
| Bot Framework | [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) 22.1 |
| Downloader | [gallery-dl](https://github.com/mikf/gallery-dl) 1.32 |
| Video Engine | [yt-dlp](https://github.com/yt-dlp/yt-dlp) (latest) |
| Container | Docker (python:3.11-slim + ffmpeg) |
| Hosting | [Railway](https://railway.app) |

## Project Structure

```
.
├── bot.py              # Main bot source (single-file architecture)
├── Dockerfile          # Production container image
├── requirements.txt    # Python dependencies
├── CHANGELOG.md        # Full history of all bug fixes and releases
├── SECURITY.md         # Security policy and vulnerability reporting
├── CONTRIBUTING.md     # Contribution guidelines
├── CODE_OF_CONDUCT.md  # Community code of conduct
├── LICENSE             # MIT License
├── .gitignore          # Git ignore rules
└── README.md           # This file
```

## Authors

| | Name | Role | Links |
|---|---|---|---|
| <a href="https://github.com/ebnycuhie"><img src="https://github.com/ebnycuhie.png" width="50" /></a> | **ebnycuhie** | Creator, Maintainer | [GitHub](https://github.com/ebnycuhie) · [Telegram](https://t.me/ebnycuhie) · [Instagram](https://instagram.com/ebnycuhie) |
| <a href="https://github.com/sayfalse"><img src="https://github.com/sayfalse.png" width="50" /></a> | **sayfalse** | Creator, Maintainer | [GitHub](https://github.com/sayfalse) · [Telegram](https://t.me/sayfalsex) · [Instagram](https://instagram.com/sudoflame) |

See all [contributors](https://github.com/copyrightnews/cuhibot/graphs/contributors).

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for community standards.

## Support

- 📢 **Telegram** — [@copyrightnews](https://t.me/copyrightnews)
- 🐛 **Bug reports** — [GitHub Issues](https://github.com/copyrightnews/cuhibot/issues)
- 🔒 **Security** — See [SECURITY.md](SECURITY.md) for responsible disclosure

## License

[MIT](LICENSE) © 2026 ebnycuhie & sayfalse
