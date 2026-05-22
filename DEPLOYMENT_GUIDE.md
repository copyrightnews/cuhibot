# 🚀 Deployment Guide - Post-Security-Fixes

**Version:** 2.3.1 (Security Hardened)  
**Date:** May 23, 2026  
**Status:** Ready for Production

---

## ✅ What Was Fixed

All **23 security issues** have been addressed:
- ✅ 2 Critical vulnerabilities
- ✅ 5 High-priority bugs  
- ✅ 7 Medium-priority issues
- ✅ 4 Low-priority issues
- ✅ 5 Technical debt items

**Your codebase is now production-ready!**

---

## 📋 Pre-Deployment Checklist

### Step 1: Install Dependencies

```bash
pip install -r requirements.txt
```

**New dependencies added:**
- `cryptography>=42.0.0` - Cookie encryption
- `slowapi>=0.1.9` - API rate limiting
- `psutil>=5.9.0` - System monitoring

### Step 2: Generate Encryption Key

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the output (it will look like: `gAAAAABh...`)

### Step 3: Configure Environment

1. **Copy the template:**
   ```bash
   cp .env.example .env
   ```

2. **Edit `.env` and fill in:**
   ```env
   BOT_TOKEN="your_actual_bot_token"
   ADMIN_IDS="your_admin_id"
   COOKIE_ENCRYPTION_KEY="paste_generated_key_here"
   PUBLIC_DOMAIN="your_domain.com"
   ```

3. **Verify `.env` is in `.gitignore`:**
   ```bash
   grep "^\.env$" .gitignore
   ```

### Step 4: Migrate Existing Cookies (If Any)

If you have existing plaintext cookies:

```bash
python migrate_cookies.py
```

This will:
- ✅ Encrypt all existing cookies
- ✅ Auto-generate encryption key if missing
- ✅ Safely delete plaintext versions
- ✅ Verify encryption worked

### Step 5: Run Tests

```bash
python -m pytest test_bot.py -v
```

Expected output: All tests should pass ✅

### Step 6: Test Locally

**Terminal 1 - Start the bot:**
```bash
python bot.py
```

**Terminal 2 - Test the server:**
```bash
curl http://localhost:8080/healthz
```

Expected response:
```json
{
  "status": "ok",
  "disk_space_ok": true,
  "data_dir_accessible": true,
  "timestamp": 1716422400.0
}
```

### Step 7: Verify Security Features

**Test rate limiting:**
```bash
# Make 11 rapid requests (limit is 10/minute)
for i in {1..11}; do
  curl -X POST http://localhost:8080/api/download \
    -H "Authorization: Bearer test_token" \
    -H "Content-Type: application/json" \
    -d '{"media_type":"all"}' &
done
```

Expected: 11th request should return `429 Too Many Requests`

**Test path traversal protection:**
```bash
curl http://localhost:8080/api/files/../../../etc/passwd \
  -H "Authorization: Bearer test_token"
```

Expected: `403 Forbidden`

**Test cookie encryption:**
```bash
# Check that cookie files have .enc extension
ls cookies/_global/
```

Expected: Files like `instagram.com_cookies.enc` (not `.txt`)

---

## 🐳 Docker Deployment

### Build Image

```bash
docker build -t cuhibot:2.3.1 .
```

### Run Container

```bash
docker run -d \
  --name cuhibot \
  -p 8080:8080 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/cookies:/app/cookies \
  --env-file .env \
  cuhibot:2.3.1
```

### Check Logs

```bash
docker logs -f cuhibot
```

---

## ☁️ Cloud Deployment (Railway/Heroku/etc.)

### Environment Variables

Set these in your cloud platform:

```env
BOT_TOKEN=your_bot_token
ADMIN_IDS=your_admin_id
COOKIE_ENCRYPTION_KEY=your_encryption_key
PUBLIC_DOMAIN=your_domain.com
PORT=8080
ENV=production
```

### Persistent Storage

Ensure these directories are persistent:
- `/app/data` - User data and settings
- `/app/cookies` - Encrypted cookie files

### Health Check Endpoint

Configure your platform to use:
- **URL:** `https://your-domain.com/healthz`
- **Method:** GET
- **Expected:** 200 OK
- **Interval:** 30 seconds

---

## 🔒 Security Verification

### Run Security Scan

```bash
pip install bandit safety
bandit -r . -ll
safety check
```

Expected: No high or critical issues

### Verify Encryption

```bash
python -c "
from crypto_utils import get_crypto
crypto = get_crypto()
test_data = 'test cookie data'
encrypted = crypto.encrypt_cookie(test_data)
decrypted = crypto.decrypt_cookie(encrypted)
assert decrypted == test_data
print('✅ Encryption working correctly')
"
```

### Check File Permissions

```bash
# .env should not be world-readable
ls -la .env
```

Expected: `-rw-------` or `-rw-r-----` (not `-rw-rw-rw-`)

---

## 📊 Monitoring Setup

### Log Files

Logs are written to stdout. Capture them:

```bash
python bot.py 2>&1 | tee bot.log
```

### Key Metrics to Monitor

1. **Rate Limit Hits** - Look for "Rate limit exceeded" in logs
2. **Failed Auth Attempts** - Look for "Invalid initData" or "Session expired"
3. **File Access Denials** - Look for "Path traversal attempt blocked"
4. **Disk Space** - Monitor `/app/data` and `/app/cookies` directories
5. **Memory Usage** - Should be stable (no leaks after task cleanup)

### Recommended Monitoring Tools

- **Logs:** Grafana Loki, ELK Stack
- **Metrics:** Prometheus + Grafana
- **Uptime:** UptimeRobot, Pingdom
- **Errors:** Sentry

---

## 🔧 Troubleshooting

### Issue: "COOKIE_ENCRYPTION_KEY not set"

**Solution:**
```bash
# Generate key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Add to .env
echo 'COOKIE_ENCRYPTION_KEY="your_key_here"' >> .env
```

### Issue: "Rate limit exceeded" errors

**Solution:**
- This is expected behavior for security
- Adjust limits in `server.py` if needed:
  ```python
  @limiter.limit("20/minute")  # Increase from 10 to 20
  ```

### Issue: "Path traversal attempt blocked"

**Solution:**
- This is security working correctly
- Check that file paths don't contain `..`, `~`, or absolute paths
- Use relative paths only

### Issue: Cookie migration fails

**Solution:**
```bash
# Check encryption key is valid
python -c "from cryptography.fernet import Fernet; Fernet(b'your_key_here')"

# Run migration with verbose logging
python migrate_cookies.py --verbose
```

### Issue: Tests failing

**Solution:**
```bash
# Install test dependencies
pip install pytest pytest-asyncio

# Run with verbose output
python -m pytest test_bot.py -v -s
```

---

## 📈 Performance Tuning

### For High Traffic

1. **Increase rate limits** (if needed):
   ```python
   # In server.py
   @limiter.limit("100/minute")  # Adjust as needed
   ```

2. **Enable caching** (future enhancement):
   ```python
   # Add Redis for session storage
   # Add CDN for static files
   ```

3. **Scale horizontally**:
   - Run multiple bot instances
   - Use load balancer
   - Shared database (SQLite → PostgreSQL)

### For Low Resources

1. **Reduce cleanup frequency**:
   ```python
   # In bot.py
   await asyncio.sleep(7200)  # Every 2 hours instead of 1
   ```

2. **Limit concurrent downloads**:
   ```python
   # In bot.py
   MAX_CONCURRENT_DOWNLOADS = 2  # Reduce from default
   ```

---

## 🎯 Post-Deployment Verification

### Day 1 Checklist

- [ ] Bot responds to `/start` command
- [ ] Rate limiting is working (test with rapid requests)
- [ ] Cookies are encrypted (check file extensions)
- [ ] Health check returns 200 OK
- [ ] Logs show no errors
- [ ] Memory usage is stable
- [ ] Disk space is sufficient

### Week 1 Checklist

- [ ] No security incidents reported
- [ ] Performance is acceptable
- [ ] Error rate is low (<1%)
- [ ] Users can download media successfully
- [ ] Session management working (no premature logouts)

### Month 1 Checklist

- [ ] Review security logs
- [ ] Check for any new vulnerabilities
- [ ] Update dependencies: `pip install -U -r requirements.txt`
- [ ] Run security scan: `bandit -r . && safety check`
- [ ] Review and rotate encryption keys if needed

---

## 📞 Support & Maintenance

### Regular Maintenance Tasks

**Weekly:**
- Check logs for errors
- Monitor disk space
- Verify backups are working

**Monthly:**
- Update dependencies
- Run security scans
- Review access logs
- Rotate encryption keys (optional)

**Quarterly:**
- Full security audit
- Performance review
- Dependency audit
- Update documentation

### Getting Help

1. **Check documentation:**
   - `SECURITY_AUDIT_REPORT.md` - Detailed security analysis
   - `BUG_TRACKER.md` - Known issues and fixes
   - `CRITICAL_FIXES.md` - Implementation details
   - `FIXES_APPLIED.md` - What was fixed

2. **Review logs:**
   ```bash
   tail -f bot.log | grep ERROR
   ```

3. **Run diagnostics:**
   ```bash
   python -c "import bot; bot.validate_environment()"
   ```

---

## ✅ Success Criteria

Your deployment is successful when:

1. ✅ All tests pass
2. ✅ Health check returns 200 OK
3. ✅ Bot responds to commands
4. ✅ Rate limiting is active
5. ✅ Cookies are encrypted
6. ✅ No security warnings in logs
7. ✅ Memory usage is stable
8. ✅ Users can download media

---

## 🎉 Congratulations!

Your Cuhibot instance is now:
- ✅ Secure (all 23 vulnerabilities fixed)
- ✅ Production-ready
- ✅ Well-documented
- ✅ Monitored
- ✅ Maintainable

**Happy deploying!** 🚀

---

**Last Updated:** May 23, 2026  
**Version:** 2.3.1 (Security Hardened)
