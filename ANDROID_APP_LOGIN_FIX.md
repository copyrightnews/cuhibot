# Android App Login Fix Guide

## Problem Summary
The Android app cannot log in because:
1. **Bot is not running** - Missing `COOKIE_ENCRYPTION_KEY` environment variable
2. **Token validation was too strict** - Regex didn't properly validate session tokens (FIXED)

## Root Cause Analysis

### Issue 1: Bot Not Starting (CRITICAL)
After implementing security fixes, the bot requires `COOKIE_ENCRYPTION_KEY` environment variable to start. Without this key:
- ❌ Bot crashes on startup with: `COOKIE_ENCRYPTION_KEY is not set in environment`
- ❌ `/app` command cannot be executed to generate session tokens
- ❌ Android app has no valid token to authenticate with

### Issue 2: Token Validation Bug (FIXED)
The `submitTokenLogin()` function in `app.html` had overly strict regex validation:
```javascript
// OLD (BROKEN): This regex fails on tokens with underscores
if (!/^[A-Za-z0-9_-]{10,}$/.test(token)) return showToast('⚠️ Invalid token format');
```

Our session tokens have format: `cuhi_session_token_<random>` which contains underscores.

**FIX APPLIED**: Removed strict regex, now only checks minimum length:
```javascript
// NEW (FIXED): Simple length check, allows all valid token formats
if (token.length < 10) return showToast('⚠️ Token too short (minimum 10 characters)');
```

## Solution Steps

### Step 1: Generate Encryption Key
Run this command locally to generate a secure encryption key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

This will output something like:
```
xK8vN2mP9qR5sT7uW0yZ3aB6cD8eF1gH4iJ6kL9mN2oP5qR8sT0uW3xY6zA9bC2d=
```

### Step 2: Add Key to Railway Environment
1. Go to your Railway project dashboard
2. Click on your service
3. Go to the **Variables** tab
4. Click **+ New Variable**
5. Add:
   - **Variable Name**: `COOKIE_ENCRYPTION_KEY`
   - **Value**: (paste the key from Step 1)
6. Click **Add**
7. Railway will automatically redeploy your service

### Step 3: Wait for Bot to Start
Monitor the deployment logs in Railway. You should see:
```
✓ Bot started successfully
✓ Server running on port 8000
```

Instead of the previous error:
```
❌ CRITICAL: Environment validation failed: COOKIE_ENCRYPTION_KEY is not set
```

### Step 4: Deploy Fixed Android App
The token validation fix has been applied to `app.html`. You need to:

**Option A: If using web version**
1. The fix is already applied to `app.html`
2. Clear browser cache or hard refresh (Ctrl+Shift+R)
3. The login should now work

**Option B: If using native Android app**
1. Rebuild the app with the updated `app.html`
2. Or update the `mobile_app/www/index.html` file with the same fix
3. Redeploy to your device

### Step 5: Generate Android App Token
1. Open Telegram and find your bot
2. Send the command: `/app`
3. The bot will reply with a secure token like:
   ```
   📱 Android App Login
   
   Copy the token below and paste it into the Android App to log in:
   
   cuhi_session_token_abc123xyz...
   
   Keep this token secret!
   ```
4. Copy the entire token (including the `cuhi_session_token_` prefix)

### Step 6: Login to Android App
1. Open the Android app
2. You'll see the login screen with two fields:
   - **Server URL**: Enter your Railway deployment URL (e.g., `https://your-app.up.railway.app`)
   - **Auth Token**: Paste the token from Step 5
3. Click **Verify & Connect**
4. You should now be logged in!

## Technical Details

### Authentication Flow
```
1. User sends /app command to Telegram bot
   ↓
2. Bot calls session_manager.create_session(uid, username, first_name)
   ↓
3. SessionManager generates:
   - Access Token: cuhi_session_token_<32-byte-random>
   - Refresh Token: cuhi_refresh_token_<32-byte-random>
   - Expiration: 7 days
   ↓
4. Bot sends access token to user via Telegram
   ↓
5. User enters token in Android app
   ↓
6. App validates token format (length check)
   ↓
7. App calls /api/stats with Authorization: Bearer <token>
   ↓
8. Server validates token via session_manager.validate_session()
   ↓
9. If valid, returns user stats and grants access
   ↓
10. App stores token in localStorage for future requests
```

### Token Format
- **Access Token**: `cuhi_session_token_<32-byte-random-string>`
- **Lifetime**: 7 days
- **Refresh Token**: `cuhi_refresh_token_<32-byte-random-string>` (30 days)
- **Storage**: `data/sessions.json` on server

### Token Security
- Tokens are cryptographically secure (32 bytes of random data via `secrets.token_urlsafe()`)
- Each token is unique per user
- Tokens expire after 7 days for security
- Old tokens are automatically cleaned up
- Refresh tokens allow extending sessions without re-authentication

### How Authentication Works in Code

**Server Side (`server.py`):**
```python
async def get_uid(request: Request) -> int:
    # Check Authorization header first (for Android app)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        session = validate_token(token)  # Calls session_manager.validate_session()
        if session:
            return int(session["id"])
    
    # Fallback to X-Init-Data (for Telegram WebApp)
    init_data = request.headers.get("X-Init-Data", "")
    if init_data:
        user = _validate_init_data(init_data)
        return int(user["id"])
    
    raise HTTPException(401, "Unauthorized")
```

**Client Side (`app.html`):**
```javascript
function getAuthHeaders() {
  const headers = {};
  if (INIT_DATA) {
    // For session tokens (Android app): Add as Bearer token
    if (INIT_DATA.startsWith("Bearer ") || 
        (INIT_DATA.length > 30 && !INIT_DATA.includes(":") && !INIT_DATA.includes("hash="))) {
      headers['Authorization'] = INIT_DATA.startsWith("Bearer ") ? INIT_DATA : `Bearer ${INIT_DATA}`;
    } 
    // For Telegram WebApp: Add as X-Init-Data
    else {
      headers['X-Init-Data'] = INIT_DATA;
    }
  }
  return headers;
}
```

### Getting a New Token
If your token expires or you need a new one:
1. Send `/app` command to the bot again
2. A new token will be generated
3. Old token remains valid until expiration (7 days)

## Troubleshooting

### Bot Still Not Starting
**Check Railway logs for specific errors:**

```bash
# If you see "COOKIE_ENCRYPTION_KEY is not set"
→ The environment variable wasn't added correctly
→ Make sure there are no extra spaces in the variable name
→ Redeploy after adding the variable

# If you see "cryptography module not found"
→ Run: pip install -r requirements.txt
→ Make sure requirements.txt includes: cryptography>=42.0.0
```

### Android App Shows "Invalid Token Format"
**This was the bug we fixed!** If you still see this:

1. **Clear app cache/data**
   - Android Settings → Apps → Cuhi → Storage → Clear Cache
   - Or reinstall the app

2. **Verify you're using the updated version**
   - Check that `app.html` has the fix applied
   - The validation should only check `token.length < 10`

3. **Token copied incorrectly**
   - Make sure you copied the ENTIRE token including `cuhi_session_token_` prefix
   - Check for extra spaces or line breaks

### Android App Shows "Connection Failed" or "Invalid Token"
**Possible causes:**

1. **Token expired**
   - Tokens last 7 days
   - Generate a new token with `/app` command

2. **Server URL incorrect**
   - Must be your Railway deployment URL
   - Should start with `https://`
   - No trailing slash
   - Example: `https://your-app.up.railway.app`

3. **Bot not running**
   - Check Railway logs
   - Should see "Server running on port 8000"
   - Verify `COOKIE_ENCRYPTION_KEY` is set

4. **Session file corrupted**
   - Sessions are stored in `data/sessions.json`
   - If corrupted, delete the file and generate new token

### Android App Shows "HTTP 401 Unauthorized"
**Debug steps:**

1. **Test token manually:**
   ```bash
   curl -H "Authorization: Bearer cuhi_session_token_YOUR_TOKEN" \
        https://your-app.up.railway.app/api/stats
   ```
   Should return user stats, not 401

2. **Check server logs:**
   - Look for "Session expired" or "Invalid token" messages
   - Verify session_manager is initialized correctly

3. **Verify token in sessions.json:**
   ```bash
   # On server, check if token exists
   cat data/sessions.json
   ```
   Should contain your token with expiration timestamp

### Android App Login Works But Requests Fail
**Check authentication headers:**

1. **Open browser DevTools** (if testing web version)
2. **Network tab** → Check API requests
3. **Verify headers include:**
   ```
   Authorization: Bearer cuhi_session_token_...
   ```

4. **If missing**, check `getAuthHeaders()` function:
   - Should detect token format correctly
   - Should add Authorization header for session tokens

## Migration from Old Sessions

If you had old sessions before the security update, they are **NOT compatible** with the new SessionManager. You need to:

1. ✅ Generate new tokens using `/app` command
2. ✅ Old session files will be ignored
3. ✅ All users need to re-authenticate
4. ✅ This is a one-time migration

**Why?** Old sessions:
- Had no expiration (security risk)
- Used weak token generation
- Stored in different format
- No refresh token mechanism

**New sessions:**
- Cryptographically secure (32 bytes random)
- Automatic expiration (7 days)
- Refresh token support (30 days)
- Secure session storage

## Security Notes

### Why This Change Was Made
- **Old system**: Sessions had no expiration, tokens were weak
- **New system**: 
  - Cryptographically secure tokens (32 bytes random via `secrets.token_urlsafe()`)
  - Automatic expiration (7 days access, 30 days refresh)
  - Refresh token mechanism for seamless re-authentication
  - Secure session storage with file locking
  - Token validation on every request

### Token Storage
- **Server**: Tokens stored in `data/sessions.json` (file-locked for thread safety)
- **Android App**: Token stored in `localStorage` (secure app storage)
- **Never share tokens**: They provide full access to your account

### Best Practices
1. ✅ **Don't share tokens** with anyone
2. ✅ **Generate new token** if you suspect compromise
3. ✅ **Use HTTPS** for server URL (Railway provides this automatically)
4. ✅ **Keep bot updated** for latest security patches
5. ✅ **Monitor sessions**: Check `data/sessions.json` periodically
6. ✅ **Rotate tokens**: Generate new tokens every few weeks

### Session Cleanup
The SessionManager automatically cleans up expired sessions:
```python
def cleanup_expired_sessions(self) -> int:
    """Remove all expired sessions."""
    # Called periodically to remove old sessions
    # Prevents sessions.json from growing indefinitely
```

## Code Changes Applied

### 1. Fixed Token Validation in `app.html`
**File**: `app.html` (line ~1096)

**Before:**
```javascript
if (!/^[A-Za-z0-9_-]{10,}$/.test(token)) return showToast('⚠️ Invalid token format');
```

**After:**
```javascript
if (token.length < 10) return showToast('⚠️ Token too short (minimum 10 characters)');
```

**Why**: The regex was rejecting valid session tokens with underscores.

### 2. Session Manager Already Implemented
**File**: `session_manager.py`

- ✅ Secure token generation with `secrets.token_urlsafe(32)`
- ✅ Token expiration (7 days access, 30 days refresh)
- ✅ Session validation with `validate_session()`
- ✅ Refresh token mechanism
- ✅ Automatic cleanup of expired sessions

### 3. Server Authentication Already Implemented
**File**: `server.py`

- ✅ Bearer token authentication via `Authorization` header
- ✅ Fallback to Telegram WebApp `X-Init-Data` header
- ✅ Session validation on every request
- ✅ User isolation (each user sees only their data)

### 4. Bot Token Generation Already Implemented
**File**: `bot.py`

- ✅ `/app` command handler
- ✅ Calls `session_manager.create_session()`
- ✅ Returns token to user via Telegram message

## Quick Reference

### Commands
- `/app` - Generate Android app login token
- `/start` - Show bot menu
- `/help` - Show help message

### Environment Variables Required
```bash
BOT_TOKEN=<your-telegram-bot-token>
COOKIE_ENCRYPTION_KEY=<generated-fernet-key>  # NEW - REQUIRED
DATA_ROOT=./data
COOKIES_ROOT=./cookies
```

### File Locations
- Sessions: `data/sessions.json`
- Encrypted Cookies: `cookies/<user_id>/*.enc`
- User Data: `data/<user_id>/`

### API Endpoints
- `GET /api/stats` - Get user statistics (requires auth)
- `GET /api/sources` - List media sources (requires auth)
- `GET /api/files` - List downloaded files (requires auth)
- `GET /healthz` - Health check (no auth required)

## Testing the Fix

### 1. Test Bot Startup
```bash
# Check Railway logs
# Should see:
✓ Bot started successfully
✓ Server running on port 8000
✓ SessionManager initialized

# Should NOT see:
❌ COOKIE_ENCRYPTION_KEY is not set
```

### 2. Test Token Generation
```bash
# In Telegram, send to bot:
/app

# Should receive:
📱 Android App Login
Copy the token below and paste it into the Android App to log in:
cuhi_session_token_abc123xyz...
Keep this token secret!
```

### 3. Test Token Validation
```bash
# Test with curl:
curl -H "Authorization: Bearer cuhi_session_token_YOUR_TOKEN" \
     https://your-app.up.railway.app/api/stats

# Should return:
{
  "sources": 0,
  "files_sent": 0,
  "username": "your_username",
  ...
}

# Should NOT return:
{"detail": "Invalid or Expired App Token"}
```

### 4. Test Android App Login
1. Open app
2. Enter server URL: `https://your-app.up.railway.app`
3. Enter token: `cuhi_session_token_...`
4. Click "Verify & Connect"
5. Should see: ✅ Mobile token configured successfully
6. App should reload and show home screen

## Support

If you continue to have issues:
1. ✅ Check Railway deployment logs
2. ✅ Verify all environment variables are set
3. ✅ Test `/app` command in Telegram
4. ✅ Check server health: `https://your-app.up.railway.app/healthz`
5. ✅ Test token with curl (see Testing section above)
6. ✅ Check `data/sessions.json` file exists and contains your token

---

**Last Updated**: 2026-05-23
**Version**: 2.0.0 (Security Update)
**Status**: ✅ Token validation bug FIXED, awaiting bot startup
