# 🛡️ Keeping Cuhi Bot Safe

Security is a huge deal for us. Since this bot handles your social media cookies and private media, we've designed it to be as private as possible.

## 🐛 If You Find a Bug
If you discover a security hole or a vulnerability, **please don't post it publicly** on the GitHub issue tracker. 

Instead, send us an email so we can fix it before it's exploited:
📧 **mintdmca@gmail.com** (Copyright News - Official Support)

We usually reply within **48 hours** and try to get a fix out in less than a week.

---

## 🛠️ Our Security Principles
We built the bot on three main rules:
1. **Least Privilege**: The bot only asks for what it absolutely needs to work.
2. **Isolation**: Your data is yours. Every user has their own isolated folder—no one else can see your links or history.
3. **Integrity**: We use file locking to make sure your data doesn't get corrupted if you're running multiple tasks at once.

---

## 📋 Security Quick-Look

| Feature | How it protects you |
|---------|---------------------|
| **Access Control** | You can lock the bot to your specific Telegram ID using `ALLOWED_USERS`. |
| **Rate Limiting** | Prevents the bot from being spammed or your accounts from being flagged. |
| **Validation** | We strictly check every URL to prevent "shell injection" or other sneaky attacks. |
| **Upload Guard** | Automatically skips files over 50MB to avoid breaking the Telegram API. |
| **Minimal Base** | Our Docker image is slim and doesn't include any unnecessary junk. |

---

## 📅 Audit History
We've done **17 rigorous audit passes** and fixed **78 bugs** to get the code where it is today. 

*For the full technical breakdown, check out our [CHANGELOG.md](CHANGELOG.md).*

---

### ⚠️ A Final Warning
**Never share your `BOT_TOKEN` or your cookie files with anyone.** If someone gets a hold of them, they can access your Telegram bot and your social media accounts. If you think they've been leaked, rotate them immediately!

---
<p align="center">Stay safe out there. ✌️</p>
