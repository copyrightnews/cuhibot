# 🛡️ Cuhi Bot Security Policy

Security is a paramount concern for the Cuhi Bot team. Since this bot is designed to handle user authentication cookies for social media platforms (Instagram, TikTok, Facebook, Twitter/X) and process private media files, it is engineered with strict privacy and security guardrails.

This document outlines our security practices, how to report vulnerabilities, and best practices for securely running your own instance of Cuhi Bot.

---

## 🚨 Reporting a Vulnerability

**DO NOT report security vulnerabilities via public GitHub issues.**

If you discover a security vulnerability in Cuhi Bot, please report it immediately via private email so that we can patch it before it is exploited in the wild.

*   **Email:** `mintdmca@gmail.com`
*   **Response Time:** We will acknowledge receipt of your vulnerability report within 48 hours.
*   **Patch Timeline:** We strive to provide a patch or mitigation within 7 days of validating the report.

Please include the following information in your report:
*   Type of vulnerability (e.g., XSS, RCE, Path Traversal, Authentication Bypass)
*   Steps to reproduce the vulnerability
*   The potential impact of the vulnerability
*   Your environment details (OS, Python version, Cuhi Bot version)

---

## 🔒 Security Principles

Cuhi Bot is built on the following core security principles:

1.  **Self-Hosted Privacy:** We do not collect telemetry, user data, or analytics. Your data, cookies, and downloaded media remain entirely on your own server.
2.  **Least Privilege:** The application runs with the minimum permissions required. We strongly recommend running Cuhi Bot in a Docker container or a dedicated, restricted user account.
3.  **Strict Isolation:** User data and history files are segregated. A user cannot access or trigger downloads using another user's cookie profile.
4.  **Data Integrity:** We utilize cooperative advisory file locking (via `O_CREAT | os.O_EXCL` helper context managers) to prevent race conditions and file corruption when multiple users or processes access the JSON data stores simultaneously.
5.  **Input Sanitization:** All URLs passed to the bot via Telegram messages or the Mini App are strictly validated against allow-listed regex patterns before being passed to the underlying download engine (`gallery-dl`).

---

## 🛡️ Access Control & Hardening

When deploying Cuhi Bot, administrators are highly encouraged to utilize the built-in security features to harden their instance:

### 1. User Allowlists
Cuhi Bot is NOT a public bot by default. You should configure the `ALLOWED_USERS` environment variable with a comma-separated list of Telegram User IDs. If a user is not on this list, the bot will actively block their access by replying with an "Access Denied" message on Telegram and restricting access to the Mini App backend/dashboard.

### 2. Admin System
The `/admin` panel is restricted via the `ADMIN_IDS` environment variable. Only these users can globally restart the bot, view system metrics, or manage global configurations.

### 3. Telegram WebApp Validation
The Mini App backend (`server.py`) does not trust client-side data. All API requests from the Mini App must include the `initData` payload from Telegram. The server cryptographically verifies this payload against your `BOT_TOKEN` using HMAC-SHA256 to ensure the request genuinely originated from the authenticated Telegram user.

### 4. Safe Payload Limits
To protect the server from Out-Of-Memory (OOM) crashes and the Telegram API from rate limits, Cuhi Bot automatically skips individual files larger than 50MB and groups uploads into maximum batches of 10 items.

---

## ⚠️ Administrator Responsibilities

While we secure the codebase, the security of the host environment is your responsibility:

*   **Keep Secrets Secret:** Never commit your `BOT_TOKEN` or `cookies.txt` files to a public repository. If your token leaks, anyone can control your bot. Revoke it immediately via [@BotFather](https://t.me/BotFather) if compromised.
*   **Secure Cookie Handling:** Social media cookies are equivalent to passwords. If a malicious actor gains access to your `cookies.txt`, they have full access to your social media accounts. Ensure the directory where cookies are stored (`/app/data/cookies`) has restrictive file permissions.
*   **Burner Accounts:** We strongly advise using "burner" or secondary social media accounts to generate cookies for Cuhi Bot. Do not use your primary personal accounts, as automated scraping can sometimes trigger anti-bot measures resulting in account suspension.
*   **Updates:** We regularly update Cuhi Bot to patch underlying dependencies (`gallery-dl`, `python-telegram-bot`, `FastAPI`). Keep your instance up to date to ensure you are protected against upstream vulnerabilities.

---
*Stay safe and keep your archives secure.*
