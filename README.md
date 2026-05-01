# 🤖 Cuhi Bot (v1.3.4 Final Stable)

![Status: Stable](https://img.shields.io/badge/Status-Stable%20%26%20Final-brightgreen)

### Reliable media archiving.

Cuhi Bot is a self-hosted Telegram bot for archiving media from Instagram, TikTok, Facebook, and X (Twitter) directly to your Telegram channels. We built this because we were tired of unreliable downloaders that break, leak data, or are unnecessarily complex.

---

## 💎 Why Cuhi Bot?

Most downloaders are "black boxes"—you don't know who has your cookies or where your data is stored. We built this to be transparent:
*   **Self-Hosted**: You own the code. Your cookies and sessions live on your own server.
*   **Async Native**: v1.3.4 introduces a full non-blocking I/O architecture, ensuring the bot stays responsive even during heavy multi-user use.
*   **Production Ready**: We use OS-level file locking and a dedicated I/O thread pool to ensure consistent performance.
*   **Stable**: This is a "set and forget" tool designed to run 24/7.

---

## 🏢 Organization

Cuhi Bot is maintained by **Copyright News**. We focus on building open-source tools that give you full control over your digital archiving.

<p align="center">
  <a href="https://github.com/copyrightnews">
    <img src="https://github.com/copyrightnews.png" width="150" style="border-radius: 15px;" alt="Copyright News"/>
  </a>
  <br />
  <a href="https://github.com/copyrightnews"><b>Copyright News</b></a>
  <br />
  <i>Open Source for Content Archival</i>
  <br />
  📧 <a href="mailto:mintdmca@gmail.com">mintdmca@gmail.com</a>
</p>

---

## ⚡ What it Does

- 📸 **Multi-platform**: Grab stuff from **Instagram**, **TikTok**, **Facebook**, and **X/Twitter**.
- 🎬 **Everything Included**: Photos, videos, stories, highlights — all cleanly packaged into 10-item media groups.
- 📡 **Auto-Forwarding**: Automatically send media to your private channels or groups.
- 🍪 **Cookie Support**: Upload your own cookies via the bot for private/age-restricted content.
- 🗂️ **Smart Archive**: It remembers what you've already downloaded so it doesn't waste bandwidth.
- 🔒 **Safe & Secure**: Admin system, user allowlists, and input validation to keep the trolls out.

---

## 🚀 Getting Started

We recommend [Railway](https://railway.app) for the easiest setup, but it runs anywhere with Docker or Python.

### The Fast Way (Railway)
1. **Fork** this repo.
2. Connect it to Railway.
3. Add a volume mounted at `/app/data` (this is where your history lives).
4. Set your `BOT_TOKEN` in the variables.
5. Deploy.

### The Manual Way
```bash
git clone https://github.com/copyrightnews/cuhibot.git
cd cuhibot
pip install -r requirements.txt
export BOT_TOKEN="your-token-here"
python bot.py
```

---

## 📝 Configuration

| Variable | What it does |
|----------|--------------|
| `BOT_TOKEN` | Your Telegram token from @BotFather. |
| `ALLOWED_USERS` | (Optional) List of IDs allowed to use the bot. |
| `ADMIN_IDS` | Your ID to access the /admin panel. |
| `DATA_ROOT` | Where your archives and history are saved. Defaults to `./data`. |

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

## 🤝 Community & Support

- 🗺️ **Roadmap**: Check out [ROADMAP.md](ROADMAP.md) to see what we're building next.
- 🛡️ **Security**: If you find a bug, read our [SECURITY.md](SECURITY.md) before posting publicly.
- 📢 **Updates**: Join us on Telegram [@copyrightnews](https://t.me/copyrightnews).
- ⚖️ **License**: MIT. Free to use, free to fork.

---
<p align="center">Made with ❤️ for the Open Source Community</p>
