# Contributing to Cuhi Bot

Thank you for considering contributing to **Cuhi Bot**! Every contribution helps make the project better.

## How to Contribute

### 🐛 Reporting Bugs

- Check the [existing issues](https://github.com/naimurnstu/x/issues) to avoid duplicates
- Use the **Bug Report** template if available
- Include: steps to reproduce, expected vs. actual behavior, error logs, and your environment

### 💡 Suggesting Features

- Open an issue with the **Feature Request** label
- Describe the use case and why it would be valuable
- Be specific about the behavior you'd like to see

### 🔧 Submitting Code

1. **Fork** the repository
2. **Create** a feature branch from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. **Make your changes** — keep commits focused and descriptive
4. **Test** your changes locally:
   ```bash
   export BOT_TOKEN="your-test-token"
   python bot.py
   ```
5. **Push** to your fork:
   ```bash
   git push origin feature/your-feature-name
   ```
6. **Open a Pull Request** against `main`

## Development Guidelines

### Code Style

- **Single-file architecture**: All bot logic lives in `bot.py`
- **Python 3.11+**: Use modern Python features (type hints, f-strings, `match` where appropriate)
- **Docstrings**: All public functions should have docstrings explaining purpose and behavior
- **Logging**: Use `logger.info()` / `logger.warning()` / `logger.error()` — never `print()`
- **Error handling**: Always catch specific exceptions; never let errors crash the bot silently

### Security

- **Never commit secrets** (`BOT_TOKEN`, cookies, API keys)
- **Validate all user input** before processing
- **Test with `ALLOWED_USERS` set** to ensure access control works
- Report security vulnerabilities privately — see [SECURITY.md](SECURITY.md)

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add support for new platform
fix: prevent crash on empty cookie file
docs: update deployment instructions
refactor: simplify flush() retry logic
```

### Pull Request Checklist

- [ ] Code follows the existing style and conventions
- [ ] No hardcoded secrets or test tokens
- [ ] All existing functionality still works
- [ ] Docstrings added/updated for new functions
- [ ] CHANGELOG.md updated if applicable

## Questions?

Feel free to reach out:

- 💬 [Telegram Channel](https://t.me/copyrightnews)
- 📧 ebnycuhie@gmail.com / nahidurrahmanx@gmail.com

---

Thank you for contributing! 🎉
