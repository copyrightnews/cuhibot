# 🗺️ Cuhi Bot Roadmap

This document outlines the planned features, improvements, and stability goals for **Cuhi Bot**. Our focus is on maintaining a production-grade experience while expanding capabilities for power users.

---

## 🟢 Phase 1: Stability & Hardening (Current)
*Focus: Zero-error reliability and atomic data management.*

- [x] **Pass 17 Audit**: Complete overhaul of state management and process lifecycles.
- [x] **Atomic RMW Pattern**: Ensure profile and setting writes never corrupt data.
- [x] **Symbolic Link Support**: Centralize app data management for transparent storage.
- [x] **Real-time Download Engine**: Move away from filesystem polling to stdout parsing.

---

## 🟡 Phase 2: User Experience & Scale (Q2 2026)
*Focus: Making the bot more intuitive and capable of handling higher volume.*

- [ ] **Multi-Account Rotation**:
  - Support for multiple cookie files per platform.
  - Automatic rotation when a specific account hits a rate limit.
- [ ] **Web Dashboard (Lite)**:
  - A minimalist web interface to manage sources and view history without using Telegram commands.
  - Powered by a local API (FastAPI) bundled with the bot.
- [ ] **Enhanced Scheduling**:
  - Custom cron-style scheduling for each source independently.
  - "Priority" sources that check more frequently.

---

## 🟠 Phase 3: Extension & Integration (Q3 2026)
*Focus: Opening the bot to power-user workflows and automation.*

- [ ] **Plugin System**:
  - Allow users to drop Python scripts into a `plugins/` folder.
  - Hooks for `on_download_complete`, `on_send_success`, etc.
- [ ] **Direct Cloud Upload**:
  - Integration with Google Drive, Dropbox, and Rclone.
  - Option to skip Telegram upload and send files directly to private storage.
- [ ] **OCR & Metadata Extraction**:
  - Automatically extract text from images and captions.
  - Searchable history based on extracted content.

---

## 🔴 Phase 4: Long-Term Vision (2027+)
*Focus: Autonomous media preservation.*

- [ ] **AI-Powered Categorization**:
  - Use local LLMs or vision models to auto-tag and organize downloaded media into themed channels.
- [ ] **P2P Backup**:
  - Optional decentralized storage of download archives (IPFS) to ensure media is never lost.

---

> [!NOTE]
> This roadmap is a living document. Features are prioritized based on community feedback in our [Telegram Channel](https://t.me/copyrightnews) and [GitHub Issues](https://github.com/copyrightnews/cuhibot/issues).
