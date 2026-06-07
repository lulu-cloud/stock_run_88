import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import backend.auth as auth


AUTH_TEST_SCHEMA = """
CREATE TABLE system_settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE telegram_binding (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id INTEGER NOT NULL,
    chat_id TEXT NOT NULL,
    username TEXT,
    enabled INTEGER DEFAULT 1
);
CREATE TABLE auth_login_code (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code_hash TEXT NOT NULL UNIQUE,
    telegram_user_id TEXT NOT NULL,
    chat_id TEXT,
    username TEXT,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    attempt_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE auth_session (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_hash TEXT NOT NULL UNIQUE,
    telegram_user_id TEXT NOT NULL,
    username TEXT,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    last_seen_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE auth_rate_limit (
    key_hash TEXT PRIMARY KEY,
    count INTEGER DEFAULT 0,
    reset_at TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);
"""


class AuthTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        conn = sqlite3.connect(self.tmp.name)
        conn.executescript(AUTH_TEST_SCHEMA)
        conn.execute(
            "INSERT INTO telegram_binding (agent_id, chat_id, username, enabled) VALUES (1, '5571308431', 'li', 1)"
        )
        conn.commit()
        conn.close()
        self.conn_patch = patch("backend.auth.get_conn", self._get_conn)
        self.conn_patch.start()

    def tearDown(self):
        self.conn_patch.stop()
        os.unlink(self.tmp.name)

    def _get_conn(self):
        conn = sqlite3.connect(self.tmp.name)
        conn.row_factory = sqlite3.Row
        return conn

    def test_login_code_is_limited_to_seeded_telegram_user(self):
        self.assertEqual(auth.get_allowed_telegram_user_ids(), ["5571308431"])

        denied = auth.generate_login_code("100", "100", "stranger")
        self.assertFalse(denied["ok"])

        granted = auth.generate_login_code("5571308431", "5571308431", "li")
        self.assertTrue(granted["ok"])
        self.assertRegex(granted["code"], r"^\d{6}$")

    def test_login_code_is_single_use(self):
        result = auth.generate_login_code("5571308431", "5571308431", "li")
        identity = auth._verify_login_code(result["code"])
        self.assertEqual(identity["telegram_user_id"], "5571308431")
        self.assertIsNone(auth._verify_login_code(result["code"]))

    def test_rate_limit_blocks_after_limit(self):
        self.assertTrue(auth._check_rate_limit("verify:1.2.3.4", limit=2, window_seconds=60))
        self.assertTrue(auth._check_rate_limit("verify:1.2.3.4", limit=2, window_seconds=60))
        self.assertFalse(auth._check_rate_limit("verify:1.2.3.4", limit=2, window_seconds=60))

    def test_auth_secret_has_no_fixed_fallback(self):
        env = {
            "AUTH_SECRET": "",
            "DASHBOARD_ADMIN_PASSWORD": "",
            "AUTH_ADMIN_PASSWORD": "",
        }
        with patch.object(auth, "TELEGRAM_BOT_TOKEN", ""), patch.dict(os.environ, env, clear=False):
            self.assertNotEqual(auth._secret(), "stock-run-auth-dev-secret")
            self.assertEqual(auth._secret(), auth._EPHEMERAL_AUTH_SECRET)


if __name__ == "__main__":
    unittest.main()
