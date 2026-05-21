# 🤖 Cuhi

### Premium Media Archival Ecosystem (Telegram Bot, Mini App & Native Android App)

Cuhi is a self-hosted premium media archival ecosystem featuring a Telegram bot, an iOS-inspired **Telegram Mini App**, and a fully optimized **Native Android App**. Archiving content from Instagram, TikTok, Facebook, and X (Twitter) has never been this seamless — delivered directly to your Telegram channels, local documents, or phone gallery.

<p align="center">
  <b>v2.3.0</b> · Stable Release · Production Hardened
</p>

---

## ✨ Why Cuhi?

Most downloaders are black boxes. You don't know who has your cookies or where your data goes. Cuhi is different:

- **Self-Hosted** — You own the code. Your cookies and sessions live on your server, not ours.
- **Async Native** — Full non-blocking I/O with a dedicated thread pool. Stays responsive under heavy multi-user load.
- **Mini App Dashboard** — A native iOS-style control panel built right inside Telegram. Manage sources, trigger downloads, view history — all without leaving the app.
- **Standalone Android Companion** — Packageable as a high-fidelity native app featuring robust offline local saving, fluid layouts, and persistent media gallery integration.
- **Production Hardened** — OS-level file locking, atomic writes, executor-backed async I/O, and zero bare exceptions.
- **Set & Forget** — Designed to run 24/7 with automatic schedule recovery after restarts.

---

## 📱 Mini App

The Cuhi Mini App is a full-featured dashboard that runs natively inside Telegram. Built with an iOS-inspired design language.

---

## 🤖 Standalone Android Native App

Cuhi is fully integrated and optimized to run as a native mobile application via Capacitor.

### Native Mobile Enhancements:
- **Secure Local Session Authentication:** Integrates seamlessly with your server via Telegram-issued secure `/app` session tokens, eliminating simulated flows or third-party OAuth overhead while keeping configuration private and direct.
- **Responsive Keyboard Viewport Integration:** Re-engineered with top-aligned flex-start structures and dynamic scroll calculations. When the virtual keyboard is shown, input fields and headers adjust gracefully with zero layout squashing or overlapping glitches.
- **Lenient Scoped Storage Permission Handlers:** Engineered specifically for modern Android 11+ and 13+ Scoped Storage guidelines. The app requests media permissions but never halts the sync loop if optional gallery permissions are disabled, writing files natively to your device's standard `Documents/Cuhi` folder.
- **Adaptive Native Settings Panel:** Dynamically hides Telegram-specific forward channels and Cron server schedulers, keeping the mobile interface clean, responsive, and tailored for standalone on-device archival.

### 📋 Release & Synchronisation Checklist
Before compiling release builds or deploying changes to mobile devices, always execute the UI synchronisation flow to prevent mirror drift:
1. Make your primary HTML modifications inside `app.html` (the absolute source of truth).
2. Propagate updates across all web and Capacitor mirror locations:
   ```bash
   python sync_ui.py
   ```
3. Bundle and compile updated assets for your native Android builds:
   ```bash
   npx cap sync
   ```
This checklist is automatically enforced in the CI test suite via automated byte-level mirror checks, ensuring that production apps never launch with missing or drifted assets.

### Features

| Feature | Description |
|---------|-------------|
| **Dashboard** | Real-time stats — sources, files sent, data used, history count, and disk usage |
| **Account Page** | View your Telegram profile photo, name, username, ID, and premium status |
| **Sources Manager** | Add and remove profiles across all platforms with one tap |
| **Download Control** | Choose media type, toggle stories/highlights/force refresh, start/stop downloads |
| **History** | Browse recent downloads with clear-all support |
| **Settings** | Configure output channel, schedule, cookies, and appearance |
| **Theme System** | Dark, Light, and Auto (follows your Telegram theme) |
| **Animations** | Spring-physics transitions, staggered content entrance, animated counters |

### Design

- iOS-authentic border radii (10px groups, 12px cards)
- Glassmorphic nav bar and tab bar with `backdrop-filter` blur
- Gradient stat card accents (blue, green, orange, purple)
- Apple Color Emoji font stack for consistent icons across platforms
- Haptic feedback on all interactions via Telegram WebApp API

---

## ⚡ What it Does

- 📸 **Multi-Platform** — Instagram, TikTok, Facebook, and X/Twitter
- 🎬 **Everything Included** — Photos, videos, stories, highlights — packaged into 10-item media groups
- 📡 **Auto-Forwarding** — Send media to your private channels or groups automatically
- ⏱️ **Scheduled Downloads** — Set 6h / 12h / 24h intervals with restart recovery
- 🍪 **Cookie Support** — Upload your own cookies for private and age-restricted content
- 🗂️ **Smart Archive** — Remembers what was downloaded to avoid duplicates
- 🔗 **Instant Links** — Use `/link <url>` for one-off downloads
- 📤 **Import/Export** — Move your sources between instances
- 🔒 **Secure** — Admin system, user allowlists, rate limiting, and URL validation

---

## 🚀 Getting Started

The ecosystem can be deployed anywhere Python 3.11+ can run (Docker, VPS, Cloud VPS, etc.).

### Docker Deployment (Recommended)

1. **Clone** the repository.
2. Build the Docker image:
   ```bash
   docker build -t cuhibot .
   ```
3. Run the container, ensuring you mount a persistent volume for caching and cookie storage:
   ```bash
    docker run -d \
      --name cuhibot \
      -v /path/to/local/data:/app/data \
      -v /path/to/local/cookies:/app/cookies \
      -e BOT_TOKEN="your-telegram-bot-token" \
      -e ALLOWED_USERS="user_id_1,user_id_2" \
      -e ADMIN_IDS="admin_id_1" \
      -e PUBLIC_DOMAIN="yourdomain.com" \
      -e PRODUCTION=1 \
      -p 8080:8080 \
      cuhibot
   ```

### Local Development & Testing (Windows)

Running the Cuhi mobile app or Mini App locally requires exposing your local machine to the internet so the mobile companion (or Telegram's servers) can reach your local FastAPI server. For this purpose, a pre-configured automation script is provided.

#### 📡 Local Request Architecture

Below is how the request and sync flow is established when running locally:

```mermaid
sequenceDiagram
    participant App as Standalone Android App
    participant CF as Cloudflare Edge (Public HTTPS)
    participant LocalHost as Local Port 8080 (bot.py/server.py)
    participant TG as Telegram API
    participant Target as Media Platforms (Insta/TikTok/X/FB)

    App->>CF: API Call (e.g. GET /api/stats, POST /api/download)
    Note over CF: Cloudflare Tunnel forwards public HTTPS to local HTTP port 8080
    CF->>LocalHost: Forwarded HTTP request
    LocalHost-->>CF: HTTP Response
    CF-->>App: Mobile App receives data/acknowledgment
    
    Note over LocalHost: bot.py queue worker processes the download request
    LocalHost->>Target: Download media via gallery-dl
    Target-->>LocalHost: Media files saved to local data/{uid}/downloads/
    
    loop Real-time Polling Sync
        App->>LocalHost: Download pending files via /api/files
        LocalHost-->>App: Stream media file bytes
        Note over App: Save to phone's standard Documents/Cuhi gallery folder
    end
```

#### 🛠️ Step-by-Step Lifecycle of `run_local.bat`

When you double-click or run [run_local.bat](file:///e:/Copyright%20News/cuhibot/run_local.bat), it performs the following steps automatically:

1. **Forceful Clean & Port Release**:
   It automatically kills any orphaned `python.exe` or `cloudflared.exe` processes from previous runs. This releases ports (like `8080`) and locks, preventing port collisions on launch.
2. **Cloudflare Tunnel Daemon**:
   It launches `cloudflared.exe` in a minimized background daemon window, instructing it to map a temporary public HTTPS tunnel to `http://localhost:8080`.
3. **Auto-Configuration Parsing (`update_env.py`)**:
   It starts [update_env.py](file:///e:/Copyright%20News/cuhibot/update_env.py), which reads `tunnel.log`, extracts the randomly generated `*.trycloudflare.com` subdomain, and automatically updates the `PUBLIC_DOMAIN` variable inside your local `.env`.
4. **Decoupled Multi-Process Execution**:
   Finally, it sets `SKIP_EMBEDDED_SERVER=1` to bypass duplicate server threads and launches the **FastAPI Backend Server** (`server.py`) and the **Telegram Bot** (`bot.py`) in separate independent terminals. Both connect cleanly on port `8080` without thread overlapping or locking conflicts.

#### 🚀 Quick Start Guide (Local Setup)

1. **Pre-requisites**:
   - Install Python 3.11+.
   - Add your Telegram Bot Token and configuration credentials in a `.env` file in the root directory.
   - Keep the included `cloudflared.exe` in the root directory.
2. **Launch Services**:
   - Double-click or run [run_local.bat](file:///e:/Copyright%20News/cuhibot/run_local.bat).
   - Wait 5–10 seconds. The console will print your public Tunnel URL and launch the **Telegram Bot** console.
3. **Link Mobile/Mini App**:
   - Run `python sync_ui.py` in the root folder, then execute `npx cap sync` in the `mobile_app/` folder to sync the UI assets and bundle the configuration.
   - Run/Compile the project inside **Android Studio** onto your device or emulator. It will automatically connect directly to your local server over the secure tunnel!

---

## 📝 Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `BOT_TOKEN` | Telegram bot token from @BotFather | Required |
| `ALLOWED_USERS` | Comma-separated list of allowed user IDs | Required in production (fails closed if empty). Optional in dev (allows all). |
| `ADMIN_IDS` | Admin user IDs for `/admin` panel | None |
| `DATA_ROOT` | Path for archives, history, and user data | `./data` |
| `COOKIES_ROOT` | Path for cookie storage | `./cookies` |
| `PUBLIC_DOMAIN` | The public domain (FQDN) for hosting the Mini App backend | None |

---

## 🏗️ Architecture

```
bot.py       — Main bot: handlers, download engine, scheduler, persistence
server.py    — FastAPI backend for Mini App (runs in daemon thread)
app.html     — Mini App frontend (single-file, zero dependencies)
```

**Key internals:**
- `ThreadPoolExecutor` for non-blocking file I/O
- `asyncio.Queue` for Mini App → Bot download communication
- `PTB JobQueue` with `post_init` recovery for scheduled tasks
- HMAC-verified `initData` authentication for all Mini App API calls

---

## 👥 The Team

We are a small group of developers passionate about open-source tools.

<p align="center">
  <a href="https://github.com/ebnycuhie">
    <img src="https://github.com/ebnycuhie.png" width="100" style="border-radius: 50%;" alt="ebnycuhie"/>
  </a>
  &nbsp;&nbsp;&nbsp;&nbsp;
  <a href="https://github.com/sayfalse">
    <img src="https://github.com/sayfalse.png" width="100" style="border-radius: 50%;" alt="sayfalse"/>
  </a>
</p>

<p align="center">
  <b>ebnycuhie</b> & <b>sayfalse</b>
  <br />
  Lead Maintainers @ Copyright News
</p>

---

## 🏢 Organization

<p align="center">
  <a href="https://github.com/Copyright-News">
    <img src="https://github.com/copyrightnews.png" width="150" style="border-radius: 15px;" alt="Copyright News"/>
  </a>
  <br />
  <a href="https://github.com/Copyright-News"><b>Copyright News</b></a>
  <br />
  <i>Open Source for Content Archival</i>
  <br />
  📧 <a href="mailto:mintdmca@gmail.com">mintdmca@gmail.com</a>
</p>

---

## 🤝 Community & Support

- 📋 **Changelog**: See [CHANGELOG.md](CHANGELOG.md) for version history
- 🗺️ **Roadmap**: Check out [ROADMAP.md](ROADMAP.md) to see what's next
- 🛡️ **Security**: Read [SECURITY.md](SECURITY.md) before reporting vulnerabilities
- 📢 **Updates**: Join [@copyrightnews](https://t.me/copyrightnews) on Telegram
- ⚖️ **License**: MIT — free to use, free to fork

---
<p align="center">Made with ❤️ for the Open Source Community</p>
