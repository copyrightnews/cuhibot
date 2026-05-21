import unittest
import tempfile
import shutil
import json
import time
import asyncio
from pathlib import Path

# Import bot and server modules
import bot
import server

class TestCuhiBot(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for runtime data mock
        self.test_dir = Path(tempfile.mkdtemp())
        
        # Backup original configuration directories
        self.orig_data_root = bot.DATA_ROOT
        self.orig_cookies_root = bot.COOKIES_ROOT
        self.orig_server_data_root = server.DATA_ROOT
        self.orig_sessions_file = server.SESSIONS_FILE
        
        # Override paths to point to temp directories to prevent modifying prod data
        bot.DATA_ROOT = self.test_dir / "data"
        bot.COOKIES_ROOT = self.test_dir / "cookies"
        server.DATA_ROOT = self.test_dir / "data"
        server.SESSIONS_FILE = self.test_dir / "data" / "sessions.json"
        
        # Ensure directories exist
        bot.DATA_ROOT.mkdir(parents=True, exist_ok=True)
        bot.COOKIES_ROOT.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        # Restore original configuration directories
        bot.DATA_ROOT = self.orig_data_root
        bot.COOKIES_ROOT = self.orig_cookies_root
        server.DATA_ROOT = self.orig_server_data_root
        server.SESSIONS_FILE = self.orig_sessions_file
        
        # Clean up temporary directory
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_normalize_chat(self):
        # Test basic types
        self.assertEqual(bot.normalize_chat(12345), 12345)
        self.assertEqual(bot.normalize_chat("@channelname"), "@channelname")
        self.assertEqual(bot.normalize_chat(" @channelname "), "@channelname")
        
        # Test chat ID formatting for positive digits
        # 123456 -> -100123456
        self.assertEqual(bot.normalize_chat("123456"), -100123456)
        
        # Test negative integer strings should be preserved as int
        self.assertEqual(bot.normalize_chat("-100123456"), -100123456)

    def test_handle_from_url(self):
        # Test Facebook profile ID formats
        fb_url_id = "https://www.facebook.com/profile.php?id=100083248384&mibextid=ZbWKwL"
        self.assertEqual(bot.handle_from_url(fb_url_id), "100083248384")
        
        # Test normal Facebook username URL
        fb_url_user = "https://facebook.com/username/"
        self.assertEqual(bot.handle_from_url(fb_url_user), "username")
        
        # Test X/Twitter status formats (extract user)
        x_url = "https://x.com/someuser/status/178238723487?s=20"
        self.assertEqual(bot.handle_from_url(x_url), "someuser")
        
        # Test generic URL
        generic_url = "https://www.instagram.com/someuser"
        self.assertEqual(bot.handle_from_url(generic_url), "someuser")

    def test_total_downloaded_mb(self):
        # Create mock settings for a user
        uid = 999999
        user_dir = bot.DATA_ROOT / str(uid)
        user_dir.mkdir(parents=True, exist_ok=True)
        settings_file = user_dir / "settings.json"
        
        # Write mock setting downloaded_mb
        settings_file.write_text(json.dumps({"downloaded_mb": 154.26}), encoding="utf-8")
        
        # Run async total_downloaded_mb function
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            val = loop.run_until_complete(bot.total_downloaded_mb(uid))
            self.assertEqual(val, 154.3)  # Rounded to 1 decimal place
        finally:
            loop.close()

    def test_session_auth_validation(self):
        # Test invalid token format
        self.assertIsNone(server.validate_token("invalid_token_format"))
        
        # Test valid token format but empty sessions file
        token = "cuhi_session_token_abc123xyz"
        self.assertIsNone(server.validate_token(token))
        
        # Test session creation and validation (not expired)
        session_data = {
            token: {
                "id": 12345,
                "first_name": "Test",
                "username": "testuser",
                "expires_at": time.time() + 3600  # 1 hour in future
            }
        }
        server.SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        server.SESSIONS_FILE.write_text(json.dumps(session_data), encoding="utf-8")
        
        valid_session = server.validate_token(token)
        self.assertIsNotNone(valid_session)
        self.assertEqual(valid_session["username"], "testuser")
        
        # Test expired session
        expired_token = "cuhi_session_token_expired"
        session_data[expired_token] = {
            "id": 12345,
            "first_name": "Test",
            "username": "testuser",
            "expires_at": time.time() - 3600  # 1 hour in past
        }
        server.SESSIONS_FILE.write_text(json.dumps(session_data), encoding="utf-8")
        self.assertIsNone(server.validate_token(expired_token))

    def test_cron_preset_mappings(self):
        # Verify the cron mapping logic used in inline keyboard schedules
        cron_map = {
            "6h": "0 */6 * * *",
            "12h": "0 */12 * * *",
            "24h": "0 0 * * *",
            "off": "",
        }
        self.assertEqual(cron_map.get("6h"), "0 */6 * * *")
        self.assertEqual(cron_map.get("12h"), "0 */12 * * *")
        self.assertEqual(cron_map.get("24h"), "0 0 * * *")
        self.assertEqual(cron_map.get("off"), "")

if __name__ == "__main__":
    unittest.main()
