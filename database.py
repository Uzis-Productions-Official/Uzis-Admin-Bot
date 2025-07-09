import sqlite3
import threading
import json
from typing import Optional, Dict, Any

DB_PATH = "database.db"
_lock = threading.Lock()

class Database:
    def __init__(self, path=DB_PATH):
        self.path = path
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        with _lock, self._conn:
            # Guild configs stored as JSON blobs
            self._conn.execute("""
            CREATE TABLE IF NOT EXISTS guild_configs (
                guild_id INTEGER PRIMARY KEY,
                config_json TEXT NOT NULL
            )
            """)

            # Infractions table for warnings, mutes, bans, etc.
            self._conn.execute("""
            CREATE TABLE IF NOT EXISTS infractions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                mod_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                reason TEXT,
                timestamp INTEGER NOT NULL
            )
            """)

    def get_guild_config(self, guild_id: int) -> Optional[Dict[str, Any]]:
        with _lock, self._conn:
            row = self._conn.execute(
                "SELECT config_json FROM guild_configs WHERE guild_id = ?",
                (guild_id,)
            ).fetchone()
            if row:
                return json.loads(row["config_json"])
            return None

    def set_guild_config(self, guild_id: int, config: Dict[str, Any]):
        config_json = json.dumps(config)
        with _lock, self._conn:
            self._conn.execute("""
            INSERT INTO guild_configs (guild_id, config_json)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET config_json=excluded.config_json
            """, (guild_id, config_json))

    def add_infraction(self, guild_id: int, user_id: int, mod_id: int, action: str, reason: Optional[str], timestamp: int):
        with _lock, self._conn:
            self._conn.execute("""
            INSERT INTO infractions (guild_id, user_id, mod_id, action, reason, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (guild_id, user_id, mod_id, action, reason, timestamp))

    def get_infractions(self, guild_id: int, user_id: int):
        with _lock, self._conn:
            rows = self._conn.execute("""
            SELECT * FROM infractions WHERE guild_id = ? AND user_id = ? ORDER BY timestamp DESC
            """, (guild_id, user_id)).fetchall()
            return [dict(row) for row in rows]

    def close(self):
        with _lock:
            self._conn.close()
