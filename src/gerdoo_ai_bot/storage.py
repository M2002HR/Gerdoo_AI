from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urlparse

import aiomysql
import aiosqlite

from gerdoo_ai_bot.types import ChatMessage


@dataclass(slots=True)
class ParsedDbUrl:
    backend: str
    sqlite_path: str | None = None
    mysql_host: str | None = None
    mysql_port: int | None = None
    mysql_user: str | None = None
    mysql_password: str | None = None
    mysql_database: str | None = None


def parse_db_url(db_url: str) -> ParsedDbUrl:
    value = db_url.strip()
    if value.startswith("sqlite+aiosqlite:///"):
        return ParsedDbUrl(backend="sqlite", sqlite_path=value[len("sqlite+aiosqlite:///") :])
    if value.startswith("sqlite:///"):
        return ParsedDbUrl(backend="sqlite", sqlite_path=value[len("sqlite:///") :])

    parsed = urlparse(value)
    if parsed.scheme not in {"mysql", "mysql+aiomysql"}:
        raise ValueError("Unsupported DB_URL. Use sqlite:///... or mysql+aiomysql://user:pass@host:3306/db")

    db_name = parsed.path.lstrip("/")
    if not parsed.hostname or not parsed.username or not db_name:
        raise ValueError("Invalid MySQL DB_URL")

    return ParsedDbUrl(
        backend="mysql",
        mysql_host=parsed.hostname,
        mysql_port=parsed.port or 3306,
        mysql_user=unquote(parsed.username),
        mysql_password=unquote(parsed.password or ""),
        mysql_database=unquote(db_name),
    )


class ChatStorage:
    def __init__(self, db_url: str) -> None:
        self.db_url = db_url
        self._parsed = parse_db_url(db_url)
        self._mysql_pool: aiomysql.Pool | None = None

    async def init(self) -> None:
        if self._parsed.backend == "sqlite":
            await self._init_sqlite()
            return

        await self._init_mysql()

    async def aclose(self) -> None:
        if self._mysql_pool is not None:
            self._mysql_pool.close()
            await self._mysql_pool.wait_closed()
            self._mysql_pool = None

    async def _init_sqlite(self) -> None:
        assert self._parsed.sqlite_path is not None
        async with aiosqlite.connect(self._parsed.sqlite_path) as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    username TEXT,
                    display_name TEXT NOT NULL,
                    selected_model TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    user_prompt TEXT NOT NULL,
                    assistant_reply TEXT,
                    error_text TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_history_user_id ON chat_history(user_id, id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ai_requests_user_id ON ai_requests(user_id, id)"
            )
            await conn.execute(
                """
                CREATE VIEW IF NOT EXISTS v_user_stats AS
                SELECT
                  u.user_id,
                  u.chat_id,
                  u.username,
                  u.display_name,
                  u.selected_model,
                  u.first_seen_at,
                  u.last_seen_at,
                  COUNT(h.id) AS history_messages,
                  COUNT(r.id) AS ai_requests
                FROM users u
                LEFT JOIN chat_history h ON h.user_id = u.user_id
                LEFT JOIN ai_requests r ON r.user_id = u.user_id
                GROUP BY
                  u.user_id,
                  u.chat_id,
                  u.username,
                  u.display_name,
                  u.selected_model,
                  u.first_seen_at,
                  u.last_seen_at
                """
            )
            await conn.commit()

    async def _init_mysql(self) -> None:
        self._mysql_pool = await aiomysql.create_pool(
            host=self._parsed.mysql_host,
            port=int(self._parsed.mysql_port or 3306),
            user=self._parsed.mysql_user,
            password=self._parsed.mysql_password,
            db=self._parsed.mysql_database,
            autocommit=True,
            minsize=1,
            maxsize=10,
            charset="utf8mb4",
        )

        async with self._mysql_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        user_id VARCHAR(128) PRIMARY KEY,
                        chat_id VARCHAR(128) NOT NULL,
                        username VARCHAR(255) NULL,
                        display_name VARCHAR(255) NOT NULL,
                        selected_model VARCHAR(255) NOT NULL,
                        first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                await cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_history (
                        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        user_id VARCHAR(128) NOT NULL,
                        role VARCHAR(32) NOT NULL,
                        content LONGTEXT NOT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        INDEX idx_chat_history_user_id (user_id, id),
                        CONSTRAINT fk_chat_history_user FOREIGN KEY (user_id) REFERENCES users(user_id)
                          ON DELETE CASCADE ON UPDATE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                await cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ai_requests (
                        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        user_id VARCHAR(128) NOT NULL,
                        model VARCHAR(255) NOT NULL,
                        user_prompt LONGTEXT NOT NULL,
                        assistant_reply LONGTEXT NULL,
                        error_text LONGTEXT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        INDEX idx_ai_requests_user_id (user_id, id),
                        CONSTRAINT fk_ai_requests_user FOREIGN KEY (user_id) REFERENCES users(user_id)
                          ON DELETE CASCADE ON UPDATE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                await cur.execute(
                    """
                    CREATE OR REPLACE VIEW v_user_stats AS
                    SELECT
                      u.user_id,
                      u.chat_id,
                      u.username,
                      u.display_name,
                      u.selected_model,
                      u.first_seen_at,
                      u.last_seen_at,
                      COALESCE(h.history_messages, 0) AS history_messages,
                      COALESCE(r.ai_requests, 0) AS ai_requests
                    FROM users u
                    LEFT JOIN (
                      SELECT user_id, COUNT(*) AS history_messages
                      FROM chat_history
                      GROUP BY user_id
                    ) h ON h.user_id = u.user_id
                    LEFT JOIN (
                      SELECT user_id, COUNT(*) AS ai_requests
                      FROM ai_requests
                      GROUP BY user_id
                    ) r ON r.user_id = u.user_id
                    """
                )

    async def ensure_user(
        self,
        *,
        user_id: str,
        chat_id: str,
        username: str | None,
        display_name: str,
        default_model: str,
    ) -> None:
        if self._parsed.backend == "sqlite":
            await self._sqlite_ensure_user(user_id, chat_id, username, display_name, default_model)
            return
        await self._mysql_ensure_user(user_id, chat_id, username, display_name, default_model)

    async def get_selected_model(self, user_id: str, default_model: str) -> str:
        if self._parsed.backend == "sqlite":
            assert self._parsed.sqlite_path is not None
            async with aiosqlite.connect(self._parsed.sqlite_path) as conn:
                row = await (
                    await conn.execute(
                        "SELECT selected_model FROM users WHERE user_id = ?",
                        (user_id,),
                    )
                ).fetchone()
                return str(row[0]) if row else default_model

        assert self._mysql_pool is not None
        async with self._mysql_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT selected_model FROM users WHERE user_id = %s", (user_id,))
                row = await cur.fetchone()
                return str(row[0]) if row else default_model

    async def set_selected_model(self, user_id: str, model: str) -> None:
        if self._parsed.backend == "sqlite":
            assert self._parsed.sqlite_path is not None
            async with aiosqlite.connect(self._parsed.sqlite_path) as conn:
                await conn.execute(
                    """
                    INSERT INTO users(user_id, chat_id, username, display_name, selected_model)
                    VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                      selected_model = excluded.selected_model,
                      last_seen_at = CURRENT_TIMESTAMP
                    """,
                    (user_id, user_id, None, user_id, model),
                )
                await conn.commit()
            return

        assert self._mysql_pool is not None
        async with self._mysql_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO users(user_id, chat_id, username, display_name, selected_model)
                    VALUES(%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                      selected_model = VALUES(selected_model),
                      last_seen_at = CURRENT_TIMESTAMP
                    """,
                    (user_id, user_id, None, user_id, model),
                )

    async def append_chat_message(self, user_id: str, role: str, content: str, keep_last: int) -> None:
        if self._parsed.backend == "sqlite":
            assert self._parsed.sqlite_path is not None
            async with aiosqlite.connect(self._parsed.sqlite_path) as conn:
                await conn.execute(
                    "INSERT INTO chat_history(user_id, role, content) VALUES(?, ?, ?)",
                    (user_id, role, content),
                )
                await conn.execute(
                    """
                    DELETE FROM chat_history
                    WHERE user_id = ? AND id NOT IN (
                        SELECT id FROM chat_history
                        WHERE user_id = ?
                        ORDER BY id DESC
                        LIMIT ?
                    )
                    """,
                    (user_id, user_id, keep_last),
                )
                await conn.commit()
            return

        assert self._mysql_pool is not None
        async with self._mysql_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO chat_history(user_id, role, content) VALUES(%s, %s, %s)",
                    (user_id, role, content),
                )
                await cur.execute(
                    """
                    DELETE h FROM chat_history h
                    LEFT JOIN (
                        SELECT id FROM (
                            SELECT id
                            FROM chat_history
                            WHERE user_id = %s
                            ORDER BY id DESC
                            LIMIT %s
                        ) AS recent
                    ) keep_ids ON h.id = keep_ids.id
                    WHERE h.user_id = %s AND keep_ids.id IS NULL
                    """,
                    (user_id, keep_last, user_id),
                )

    async def get_recent_chat(self, user_id: str, limit: int) -> list[ChatMessage]:
        if self._parsed.backend == "sqlite":
            assert self._parsed.sqlite_path is not None
            async with aiosqlite.connect(self._parsed.sqlite_path) as conn:
                rows = await (
                    await conn.execute(
                        """
                        SELECT role, content
                        FROM chat_history
                        WHERE user_id = ?
                        ORDER BY id DESC
                        LIMIT ?
                        """,
                        (user_id, limit),
                    )
                ).fetchall()
            return [ChatMessage(role=str(row[0]), content=str(row[1])) for row in reversed(rows)]

        assert self._mysql_pool is not None
        async with self._mysql_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT role, content
                    FROM chat_history
                    WHERE user_id = %s
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                rows = await cur.fetchall()
        return [ChatMessage(role=str(row[0]), content=str(row[1])) for row in reversed(rows)]

    async def clear_chat(self, user_id: str) -> None:
        if self._parsed.backend == "sqlite":
            assert self._parsed.sqlite_path is not None
            async with aiosqlite.connect(self._parsed.sqlite_path) as conn:
                await conn.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
                await conn.commit()
            return

        assert self._mysql_pool is not None
        async with self._mysql_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM chat_history WHERE user_id = %s", (user_id,))

    async def log_ai_request(
        self,
        *,
        user_id: str,
        model: str,
        user_prompt: str,
        assistant_reply: str | None,
        error_text: str | None,
    ) -> None:
        if self._parsed.backend == "sqlite":
            assert self._parsed.sqlite_path is not None
            async with aiosqlite.connect(self._parsed.sqlite_path) as conn:
                await conn.execute(
                    """
                    INSERT INTO ai_requests(user_id, model, user_prompt, assistant_reply, error_text)
                    VALUES(?, ?, ?, ?, ?)
                    """,
                    (user_id, model, user_prompt, assistant_reply, error_text),
                )
                await conn.commit()
            return

        assert self._mysql_pool is not None
        async with self._mysql_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO ai_requests(user_id, model, user_prompt, assistant_reply, error_text)
                    VALUES(%s, %s, %s, %s, %s)
                    """,
                    (user_id, model, user_prompt, assistant_reply, error_text),
                )

    async def _sqlite_ensure_user(
        self,
        user_id: str,
        chat_id: str,
        username: str | None,
        display_name: str,
        default_model: str,
    ) -> None:
        assert self._parsed.sqlite_path is not None
        async with aiosqlite.connect(self._parsed.sqlite_path) as conn:
            await conn.execute(
                """
                INSERT INTO users(user_id, chat_id, username, display_name, selected_model)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                  chat_id = excluded.chat_id,
                  username = excluded.username,
                  display_name = excluded.display_name,
                  last_seen_at = CURRENT_TIMESTAMP
                """,
                (user_id, chat_id, username, display_name, default_model),
            )
            await conn.commit()

    async def _mysql_ensure_user(
        self,
        user_id: str,
        chat_id: str,
        username: str | None,
        display_name: str,
        default_model: str,
    ) -> None:
        assert self._mysql_pool is not None
        async with self._mysql_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO users(user_id, chat_id, username, display_name, selected_model)
                    VALUES(%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                      chat_id = VALUES(chat_id),
                      username = VALUES(username),
                      display_name = VALUES(display_name),
                      last_seen_at = CURRENT_TIMESTAMP
                    """,
                    (user_id, chat_id, username, display_name, default_model),
                )
