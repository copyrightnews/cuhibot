# 🤖 Cuhi Bot

### Media archiving, reimagined.

Cuhi Bot is a self-hosted Telegram bot and **Mini App** for archiving media from Instagram, TikTok, Facebook, and X (Twitter) — delivered directly to your Telegram channels with a premium iOS-inspired control panel.

<p align="center">
  <b>v2.0.2</b> · Stable Release · Production Hardened
</p>

---

## ✨ Why Cuhi Bot?

Most downloaders are black boxes. You don't know who has your cookies or where your data goes. Cuhi is different:

- **Self-Hosted** — You own the code. Your cookies and sessions live on your server, not ours.
- **Async Native** — Full non-blocking I/O with a dedicated thread pool. Stays responsive under heavy multi-user load.
- **Mini App Dashboard** — A native iOS-style control panel built right inside Telegram. Manage sources, trigger downloads, view history — all without leaving the app.
- **Production Hardened** — OS-level file locking, atomic writes, executor-backed async I/O, and zero bare exceptions.
- **Set & Forget** — Designed to run 24/7 with automatic schedule recovery after restarts.

---

## 📱 Mini App

The Cuhi Mini App is a full-featured dashboard that runs natively inside Telegram. Built with an iOS-inspired design language.

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

### Manual

```bash
git clone https://github.com/Copyright-News/cuhibot.git
cd cuhibot
pip install -r requirements.txt
export BOT_TOKEN="your-token-here"
python bot.py
```

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
