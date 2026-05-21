# Contributing to Cuhi Bot

First off, thank you for considering contributing to Cuhi Bot! It's people like you that make Cuhi Bot such a great tool for the open-source community.

The following is a set of comprehensive guidelines for contributing to Cuhi Bot. These are mostly guidelines, not strict rules. Use your best judgment, and feel free to propose changes to this document in a pull request.

---

## Code of Conduct

This project and everyone participating in it is governed by the [Cuhi Bot Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code. Please report unacceptable behavior to `mintdmca@gmail.com`.

---

## How Can I Contribute?

### 🐛 Reporting Bugs

This section guides you through submitting a bug report for Cuhi Bot. Following these guidelines helps maintainers and the community understand your report, reproduce the behavior, and find related reports.

Before creating bug reports, please check the [existing issues](https://github.com/copyrightnews/cuhibot/issues) as you might find out that you don't need to create one. When you are creating a bug report, please include as many details as possible:

* **Use a clear and descriptive title** for the issue to identify the problem.
* **Describe the exact steps which reproduce the problem** in as many details as possible.
* **Provide specific examples to demonstrate the steps.** Include links to files or copy/pasteable snippets, which you use in those examples.
* **Describe the behavior you observed after following the steps** and point out what exactly is the problem with that behavior.
* **Explain which behavior you expected to see instead and why.**
* **Include Error Logs:** Provide the stack trace from your terminal.

### 💡 Suggesting Enhancements

Enhancement suggestions track new features or improvements to existing features. Before creating enhancement suggestions, please check the [existing issues](https://github.com/copyrightnews/cuhibot/issues).

When you are creating an enhancement suggestion, please include as many details as possible:

* **Use a clear and descriptive title** for the issue to identify the suggestion.
* **Provide a step-by-step description of the suggested enhancement** in as many details as possible.
* **Describe the current behavior** and **explain which behavior you expected to see instead** and why.
* **Explain why this enhancement would be useful** to most Cuhi Bot users.

### 💻 Pull Requests

The process described here has several goals:
- Maintain Cuhi Bot's quality
- Fix problems that are important to users
- Engage the community in working toward the best possible Cuhi Bot
- Enable a sustainable system for Cuhi Bot's maintainers to review contributions

Please follow these steps to have your contribution considered by the maintainers:

1. **Fork** the repository and clone your fork locally.
2. **Create a branch** for your edits (`git checkout -b feature/amazing-feature` or `bugfix/issue-number`).
3. **Write code** following our Styleguides (see below).
4. **Test** your changes locally:
   - **Local Execution:** Set your Telegram bot token and enable development mode so the bot bypasses the `ALLOWED_USERS` fail-closed check:
     ```bash
     export BOT_TOKEN="your-test-token"
     export ENV="development"  # Bypasses the ALLOWED_USERS check (alternatively set DEV=1)
     python bot.py
     ```
   - **Run Automated Tests:** Run the comprehensive unit test suite to verify code correctness and compatibility:
     ```bash
     python -m unittest test_bot.py
     ```
   - **UI Synchronization:** If you modified `app.html` (the source of truth for the Mini App UI), execute the synchronization script to propagate your updates across all mirrors before committing, otherwise the CI test will fail:
     ```bash
     python sync_ui.py
     ```
5. **Commit** your changes using descriptive commit messages.
6. **Push** your branch to your fork on GitHub.
7. **Open a Pull Request** against the `main` branch.

---

## Development Styleguides

### Python Architecture Guidelines

*   **Single-file backend**: All Telegram Bot logic and background tasks live in `bot.py`.
*   **API Server**: The FastAPI backend for the Mini App lives in `server.py`.
*   **Frontend**: The entire Telegram Mini App UI is built in `app.html` without external JS framework dependencies to maintain speed and simplicity.
*   **Async/Await**: Cuhi Bot relies heavily on asynchronous programming to prevent blocking the event loop. I/O-bound tasks (like saving JSON, reading configurations) must be offloaded to `ThreadPoolExecutor` or `asyncio.to_thread`.
*   **Exceptions**: Avoid bare `except:` blocks. Always specify the exception type. Catch `Exception` only when logging general failures, and never suppress `KeyboardInterrupt` or `SystemExit`.

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/) for your commit messages:

*   `feat:` A new feature
*   `fix:` A bug fix
*   `docs:` Documentation only changes
*   `style:` Changes that do not affect the meaning of the code (white-space, formatting, missing semi-colons, etc)
*   `refactor:` A code change that neither fixes a bug nor adds a feature
*   `perf:` A code change that improves performance
*   `test:` Adding missing tests or correcting existing tests

### Documentation

*   Update the `README.md` with details of changes to the interface, this includes new environment variables, exposed ports, useful file locations, and container parameters.
*   Update `CHANGELOG.md` when submitting a Pull Request that implements a feature or fixes a bug.

---

## Getting Help

If you have questions or need help with a contribution, please reach out to the lead maintainers:
- **Telegram:** [@copyrightnews](https://t.me/copyrightnews)
- **Email:** `mintdmca@gmail.com`

Thank you for contributing! 🎉
