# 🤖 Cuhi

### Premium Media Archival Ecosystem (Telegram Bot, Mini App & Native Android App)

Cuhi is a self-hosted premium media archival ecosystem featuring a Telegram bot, an iOS-inspired **Telegram Mini App**, and a fully optimized **Native Android App**. Archiving content from Instagram, TikTok, Facebook, and X (Twitter) has never been this seamless — delivered directly to your Telegram channels, local documents, or phone gallery.

<p align="center">
  <b>v2.1.0</b> · Stable Release · Production Hardened
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
- **Interactive Google OAuth Selector:** Features a gorgeous, Google-designed Account Selector modal that mimics authentic OAuth flows. Easily switch between cached suggested profiles or add new custom emails seamlessly.
- **Responsive Keyboard Viewport Integration:** Re-engineered with top-aligned flex-start structures and dynamic scroll calculations. When the virtual keyboard is shown, input fields and headers adjust gracefully with zero layout squashing or overlapping glitches.
- **Lenient Scoped Storage Permission Handlers:** Engineered specifically for modern Android 11+ and 13+ Scoped Storage guidelines. The app requests media permissions but never halts the sync loop if optional gallery permissions are disabled, writing files natively to your device's standard `Documents/Cuhi` folder.
- **Adaptive Native Settings Panel:** Dynamically hides Telegram-specific forward channels and Cron server schedulers, keeping the mobile interface clean, responsive, and tailored for standalone on-device archival.

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

We recommend [Railway](https://railway.app) for the easiest setup, but it runs anywhere with Python 3.11+.

### Railway (Recommended)

1. **Fork** this repo
2. Connect it to a new Railway project
3. Add a persistent volume mounted at `/app/data`
4. Set environment variables:
   - `BOT_TOKEN` — from @BotFather
   - `ALLOWED_USERS` — comma-separated Telegram user IDs
   - `ADMIN_IDS` — your Telegram ID
5. Deploy

### Local Development (Windows)

For running the ecosystem locally on a Windows PC (for testing or development), a pre-configured automation script is provided:

1. **Pre-requisites**:
   - Install Python 3.11+.
   - Add your Telegram Bot token and configurations in `.env`.
   - Ensure the included `cloudflared.exe` is present in the root folder (or install `cloudflared` on your path).

2. **Launch with One Click**:
   Double-click or execute [run_local.bat](file:///e:/Copyright%20News/cuhibot/run_local.bat) in your command prompt:
   ```bash
   run_local.bat
   ```

3. **How it works under the hood**:
   - **Service Cleanup**: On start, the script kills any stale/orphaned `python.exe` or `cloudflared.exe` processes to release ports (like port `8080`) and file locks.
   - **Tunnel Provisioning**: It runs a Cloudflare Quick Tunnel using `cloudflared.exe tunnel --url http://localhost:8080`, generating a public HTTPS URL (redirected to your local port 8080) and writing output to `tunnel.log`.
   - **Auto-Configurator**: The script runs [update_env.py](file:///e:/Copyright%20News/cuhibot/update_env.py), which parses the newly generated `trycloudflare.com` URL from `tunnel.log` and automatically writes/updates `RAILWAY_PUBLIC_DOMAIN="[your-tunnel-id].trycloudflare.com"` in your `.env`.
   - **Unified Application Boot**: Finally, the script boots [bot.py](file:///e:/Copyright%20News/cuhibot/bot.py) in a new window. The bot reads `.env`, starts the Telegram polling loop, and automatically hosts the FastAPI Web App internally in a background daemon thread on port 8080.
   - **Instant Integration**: Both the Telegram Bot's Mini App button and the Standalone Android App will now connect seamlessly to the public Cloudflare tunnel URL, forwarding requests directly to your local FastAPI server.

---

## 📝 Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `BOT_TOKEN` | Telegram bot token from @BotFather | Required |
| `ALLOWED_USERS` | Comma-separated list of allowed user IDs | All users |
| `ADMIN_IDS` | Admin user IDs for `/admin` panel | None |
| `DATA_ROOT` | Path for archives, history, and user data | `./data` |
| `COOKIES_ROOT` | Path for cookie storage | `./cookies` |
| `RAILWAY_PUBLIC_DOMAIN` | Auto-set by Railway for Mini App hosting | Auto |

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
