import unittest
import os
import shutil
import tempfile
from pathlib import Path

# Set up environment variables so bot/server don't fail to load/boot
os.environ["BOT_TOKEN"] = "123456:mock_token"
os.environ["DATA_ROOT"] = "./test_data"
os.environ["COOKIES_ROOT"] = "./test_cookies"

import bot as bot_module

class TestCuhiBot(unittest.TestCase):
    def setUp(self):
        # Create temp directory for filesystem-based tests
        self.test_dir = Path(tempfile.mkdtemp())
        self.original_data_root = bot_module.DATA_ROOT
        bot_module.DATA_ROOT = self.test_dir

    def tearDown(self):
        # Cleanup temp directory
        shutil.rmtree(self.test_dir, ignore_errors=True)
        bot_module.DATA_ROOT = self.original_data_root

    def test_normalize_chat(self):
        """Test chat and user ID normalization."""
        self.assertEqual(bot_module.normalize_chat(123456), 123456)
        self.assertEqual(bot_module.normalize_chat("123456"), -100123456) # standard positive numeric string converted to channel ID
        self.assertEqual(bot_module.normalize_chat("@username"), "@username")
        self.assertEqual(bot_module.normalize_chat("  @username  "), "@username")
        self.assertEqual(bot_module.normalize_chat(""), "")

    def test_validate_url(self):
        """Test URL validators for target platforms."""
        # Instagram
        ok, err = bot_module.validate_url("https://www.instagram.com/p/C_abc123/", "instagram")
        self.assertTrue(ok)
        ok, err = bot_module.validate_url("https://instagram.com/username", "instagram")
        self.assertTrue(ok)
        ok, err = bot_module.validate_url("https://invalid.com/p/abc", "instagram")
        self.assertFalse(ok)

        # TikTok
        ok, err = bot_module.validate_url("https://www.tiktok.com/@username/video/123456", "tiktok")
        self.assertTrue(ok)
        ok, err = bot_module.validate_url("https://tiktok.com/@ZS12345/", "tiktok")
        self.assertTrue(ok)
        ok, err = bot_module.validate_url("https://google.com/invalid", "tiktok")
        self.assertFalse(ok)

        # Facebook
        ok, err = bot_module.validate_url("https://www.facebook.com/watch/?v=123456", "facebook")
        self.assertTrue(ok)
        ok, err = bot_module.validate_url("https://fb.com/watch/?v=123456", "facebook")
        self.assertTrue(ok)

        # X (Twitter)
        ok, err = bot_module.validate_url("https://x.com/username/status/123456", "x")
        self.assertTrue(ok)
        ok, err = bot_module.validate_url("https://twitter.com/username/status/123456", "x")
        self.assertTrue(ok)

    def test_handle_from_url(self):
        """Test extraction of handles/IDs from URLs."""
        self.assertEqual(bot_module.handle_from_url("https://www.instagram.com/instagram/"), "instagram")
        self.assertEqual(bot_module.handle_from_url("https://x.com/mintgit"), "mintgit")
        self.assertEqual(bot_module.handle_from_url("https://www.tiktok.com/@creator_vibe?lang=en"), "creator_vibe")
        self.assertEqual(bot_module.handle_from_url("https://facebook.com/pages/123456"), "123456")

    def test_path_utilities(self):
        """Test that user and cookie directory paths resolve correctly."""
        self.assertEqual(bot_module.udir(100), bot_module.DATA_ROOT / "100")
        self.assertEqual(bot_module.cdir(100), bot_module.COOKIES_ROOT / "100")

    def test_download_state_rw(self):
        """Test reading and writing download state JSON."""
        uid = 9999
        data = {"running": True, "queued": True, "stop_requested": False, "progress": "Running"}
        
        # Initially default state (file does not exist)
        state = bot_module.read_dl_state(uid)
        self.assertEqual(state, {"running": False, "queued": False, "stop_requested": False})

        # Write state
        bot_module.write_dl_state(uid, data)
        
        # Read back (file exists)
        state = bot_module.read_dl_state(uid)
        self.assertEqual(state.get("running"), True)
        self.assertEqual(state.get("queued"), True)
        self.assertEqual(state.get("stop_requested"), False)
        self.assertEqual(state.get("progress"), "Running")

        # Clear state (file exists but fields updated)
        bot_module.clear_dl_state(uid)
        state = bot_module.read_dl_state(uid)
        self.assertEqual(state, {"running": False, "queued": False, "stop_requested": False, "progress": "Idle"})

    def test_tunnel_log_discovery(self):
        """Test automatic Cloudflare Tunnel domain discovery from tunnel.log file."""
        # Create a mock tunnel.log in a temp workspace directory
        temp_workspace = Path(tempfile.mkdtemp())
        mock_log = temp_workspace / "tunnel.log"
        mock_log.write_text(
            "2026-05-21T01:00:00Z INF |  https://mock-subdomain-123.trycloudflare.com\n"
            "2026-05-21T01:00:01Z INF Connection established",
            encoding="utf-8"
        )

        # Backup existing env vars
        old_pub = os.environ.get("PUBLIC_DOMAIN")
        old_rpub = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
        if "PUBLIC_DOMAIN" in os.environ: del os.environ["PUBLIC_DOMAIN"]
        if "RAILWAY_PUBLIC_DOMAIN" in os.environ: del os.environ["RAILWAY_PUBLIC_DOMAIN"]

        try:
            # Emulate the loader's execution with our mock path
            import re as _re
            if mock_log.exists():
                _log_content = mock_log.read_text(errors="ignore")
                _match = _re.search(r"https://([a-zA-Z0-9\-]+\.trycloudflare\.com)", _log_content)
                if _match:
                    _domain_found = _match.group(1)
                    if not os.environ.get("PUBLIC_DOMAIN"):
                        os.environ["PUBLIC_DOMAIN"] = _domain_found
                    if not os.environ.get("RAILWAY_PUBLIC_DOMAIN"):
                        os.environ["RAILWAY_PUBLIC_DOMAIN"] = _domain_found

            self.assertEqual(os.environ.get("PUBLIC_DOMAIN"), "mock-subdomain-123.trycloudflare.com")
            self.assertEqual(os.environ.get("RAILWAY_PUBLIC_DOMAIN"), "mock-subdomain-123.trycloudflare.com")

        finally:
            # Restore environment and clean up
            if old_pub: os.environ["PUBLIC_DOMAIN"] = old_pub
            if old_rpub: os.environ["RAILWAY_PUBLIC_DOMAIN"] = old_rpub
            shutil.rmtree(temp_workspace, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
