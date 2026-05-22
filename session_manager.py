"""
Secure session management with token rotation and expiration.
"""
import json
import logging
import secrets
import time
from pathlib import Path
from typing import Optional, Dict
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Session configuration
SESSION_LIFETIME_DAYS = 7  # Reduced from 30 to 7 days
REFRESH_TOKEN_LIFETIME_DAYS = 30
SESSION_TOKEN_PREFIX = "cuhi_session_token_"
REFRESH_TOKEN_PREFIX = "cuhi_refresh_token_"


@contextmanager
def locked_file_simple(target: Path):
    """Simple file lock for session management."""
    import sys
    lock_path = target.with_suffix(target.suffix + ".lock")
    fp = open(lock_path, "a+", encoding="utf-8")
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fp.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        fp.close()
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass


class SessionManager:
    """Manages user sessions with secure token generation and rotation."""
    
    def __init__(self, sessions_file: Path):
        self.sessions_file = sessions_file
        self.sessions_file.parent.mkdir(parents=True, exist_ok=True)
    
    def create_session(self, user_id: int, username: str, first_name: str) -> Dict[str, str]:
        """
        Create a new session with access and refresh tokens.
        
        Returns:
            Dict with 'access_token' and 'refresh_token'
        """
        access_token = f"{SESSION_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
        refresh_token = f"{REFRESH_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
        
        session_data = {
            "id": user_id,
            "username": username,
            "first_name": first_name,
            "created_at": time.time(),
            "expires_at": time.time() + (86400 * SESSION_LIFETIME_DAYS),
            "refresh_token": refresh_token,
            "refresh_expires_at": time.time() + (86400 * REFRESH_TOKEN_LIFETIME_DAYS),
        }
        
        with locked_file_simple(self.sessions_file):
            sessions = self._read_sessions()
            sessions[access_token] = session_data
            self._write_sessions(sessions)
        
        logger.info("Created session for user %s (uid=%s)", username, user_id)
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": SESSION_LIFETIME_DAYS * 86400,
        }
    
    def validate_session(self, token: str) -> Optional[Dict]:
        """
        Validate a session token.
        
        Returns:
            Session data if valid, None if invalid/expired
        """
        if not token or not token.startswith(SESSION_TOKEN_PREFIX):
            return None
        
        with locked_file_simple(self.sessions_file):
            sessions = self._read_sessions()
            session = sessions.get(token)
            
            if not session:
                return None
            
            # Check expiration
            if time.time() > session.get("expires_at", 0):
                logger.info("Session expired for uid=%s", session.get("id"))
                del sessions[token]
                self._write_sessions(sessions)
                return None
            
            return session
    
    def refresh_session(self, refresh_token: str) -> Optional[Dict[str, str]]:
        """
        Refresh an expired session using refresh token.
        
        Returns:
            New access and refresh tokens if valid, None otherwise
        """
        if not refresh_token or not refresh_token.startswith(REFRESH_TOKEN_PREFIX):
            return None
        
        with locked_file_simple(self.sessions_file):
            sessions = self._read_sessions()
            
            # Find session with matching refresh token
            old_access_token = None
            session_data = None
            
            for access_token, data in sessions.items():
                if data.get("refresh_token") == refresh_token:
                    old_access_token = access_token
                    session_data = data
                    break
            
            if not session_data:
                return None
            
            # Check refresh token expiration
            if time.time() > session_data.get("refresh_expires_at", 0):
                logger.info("Refresh token expired for uid=%s", session_data.get("id"))
                if old_access_token:
                    del sessions[old_access_token]
                self._write_sessions(sessions)
                return None
            
            # Create new tokens
            new_access_token = f"{SESSION_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
            new_refresh_token = f"{REFRESH_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
            
            # Update session
            session_data.update({
                "expires_at": time.time() + (86400 * SESSION_LIFETIME_DAYS),
                "refresh_token": new_refresh_token,
                "refresh_expires_at": time.time() + (86400 * REFRESH_TOKEN_LIFETIME_DAYS),
            })
            
            # Remove old token, add new one
            if old_access_token:
                del sessions[old_access_token]
            sessions[new_access_token] = session_data
            
            self._write_sessions(sessions)
            
            logger.info("Refreshed session for uid=%s", session_data.get("id"))
            return {
                "access_token": new_access_token,
                "refresh_token": new_refresh_token,
                "expires_in": SESSION_LIFETIME_DAYS * 86400,
            }
    
    def invalidate_session(self, token: str) -> bool:
        """
        Invalidate (logout) a session.
        
        Returns:
            True if session was found and invalidated, False otherwise
        """
        with locked_file_simple(self.sessions_file):
            sessions = self._read_sessions()
            if token in sessions:
                uid = sessions[token].get("id")
                del sessions[token]
                self._write_sessions(sessions)
                logger.info("Invalidated session for uid=%s", uid)
                return True
            return False
    
    def cleanup_expired_sessions(self) -> int:
        """
        Remove all expired sessions.
        
        Returns:
            Number of sessions removed
        """
        with locked_file_simple(self.sessions_file):
            sessions = self._read_sessions()
            now = time.time()
            
            expired = [
                token for token, data in sessions.items()
                if now > data.get("expires_at", 0)
            ]
            
            for token in expired:
                del sessions[token]
            
            if expired:
                self._write_sessions(sessions)
                logger.info("Cleaned up %d expired sessions", len(expired))
            
            return len(expired)
    
    def _read_sessions(self) -> Dict:
        """Read sessions from file."""
        if not self.sessions_file.exists():
            return {}
        try:
            return json.loads(self.sessions_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("Failed to read sessions: %s", e)
            return {}
    
    def _write_sessions(self, sessions: Dict) -> None:
        """Write sessions to file."""
        try:
            self.sessions_file.write_text(
                json.dumps(sessions, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            logger.error("Failed to write sessions: %s", e)
