from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
from urllib.parse import unquote, urlparse

import aiosqlite
try:
    import aiomysql
except ModuleNotFoundError:  # pragma: no cover - mysql backend optional in sqlite-only setups
    aiomysql = None  # type: ignore[assignment]

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
        self._mysql_pool: Any | None = None

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
                """
                CREATE TABLE IF NOT EXISTS bot_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    user_id TEXT,
                    chat_id TEXT,
                    content_type TEXT,
                    status TEXT NOT NULL,
                    error_code TEXT,
                    latency_ms REAL,
                    details_json TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS image_generations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    generation_id TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    original_prompt TEXT NOT NULL,
                    enhanced_prompt TEXT NOT NULL,
                    revised_prompt TEXT,
                    model TEXT,
                    provider TEXT,
                    image_size TEXT,
                    image_quality TEXT,
                    image_seed INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    feedback_type TEXT NOT NULL,
                    details_json TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS voice_transcriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    topic TEXT,
                    raw_transcript TEXT NOT NULL,
                    cleaned_transcript TEXT NOT NULL,
                    analysis_reply TEXT,
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
                "CREATE INDEX IF NOT EXISTS idx_bot_events_created_at ON bot_events(created_at)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bot_events_type_status ON bot_events(event_type, status)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_image_generations_user_id ON image_generations(user_id, id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_feedback_target ON user_feedback(target_type, target_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_voice_transcriptions_user_id ON voice_transcriptions(user_id, id)"
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
            await conn.execute(
                """
                CREATE VIEW IF NOT EXISTS v_bot_event_daily_stats AS
                SELECT
                  DATE(created_at) AS day,
                  event_type,
                  status,
                  COUNT(*) AS total_events,
                  AVG(COALESCE(latency_ms, 0)) AS avg_latency_ms
                FROM bot_events
                GROUP BY DATE(created_at), event_type, status
                """
            )
            await conn.commit()

    async def _init_mysql(self) -> None:
        if aiomysql is None:
            raise RuntimeError("aiomysql is required for MySQL DB_URL. Install dependencies from requirements.txt")
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
                await cur.execute("SET sql_notes = 0")
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
                    CREATE TABLE IF NOT EXISTS bot_events (
                        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        event_type VARCHAR(128) NOT NULL,
                        user_id VARCHAR(128) NULL,
                        chat_id VARCHAR(128) NULL,
                        content_type VARCHAR(64) NULL,
                        status VARCHAR(32) NOT NULL,
                        error_code VARCHAR(128) NULL,
                        latency_ms DOUBLE NULL,
                        details_json JSON NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        INDEX idx_bot_events_created_at (created_at),
                        INDEX idx_bot_events_type_status (event_type, status)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                await cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS image_generations (
                        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        generation_id VARCHAR(64) NOT NULL,
                        user_id VARCHAR(128) NOT NULL,
                        chat_id VARCHAR(128) NOT NULL,
                        original_prompt LONGTEXT NOT NULL,
                        enhanced_prompt LONGTEXT NOT NULL,
                        revised_prompt LONGTEXT NULL,
                        model VARCHAR(255) NULL,
                        provider VARCHAR(128) NULL,
                        image_size VARCHAR(32) NULL,
                        image_quality VARCHAR(32) NULL,
                        image_seed BIGINT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE KEY uq_image_generations_generation_id (generation_id),
                        INDEX idx_image_generations_user_id (user_id, id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                await cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_feedback (
                        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        user_id VARCHAR(128) NOT NULL,
                        chat_id VARCHAR(128) NOT NULL,
                        target_type VARCHAR(64) NOT NULL,
                        target_id VARCHAR(128) NOT NULL,
                        feedback_type VARCHAR(32) NOT NULL,
                        details_json JSON NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        INDEX idx_user_feedback_target (target_type, target_id),
                        INDEX idx_user_feedback_user_id (user_id, id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                await cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS voice_transcriptions (
                        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        request_id VARCHAR(64) NOT NULL,
                        user_id VARCHAR(128) NOT NULL,
                        chat_id VARCHAR(128) NOT NULL,
                        mode VARCHAR(32) NOT NULL,
                        topic VARCHAR(512) NULL,
                        raw_transcript LONGTEXT NOT NULL,
                        cleaned_transcript LONGTEXT NOT NULL,
                        analysis_reply LONGTEXT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE KEY uq_voice_transcriptions_request_id (request_id),
                        INDEX idx_voice_transcriptions_user_id (user_id, id)
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
                await cur.execute(
                    """
                    CREATE OR REPLACE VIEW v_bot_event_daily_stats AS
                    SELECT
                      DATE(created_at) AS day,
                      event_type,
                      status,
                      COUNT(*) AS total_events,
                      AVG(COALESCE(latency_ms, 0)) AS avg_latency_ms
                    FROM bot_events
                    GROUP BY DATE(created_at), event_type, status
                    """
                )
                await cur.execute("SET sql_notes = 1")

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

    async def log_bot_event(
        self,
        *,
        event_type: str,
        status: str,
        user_id: str | None = None,
        chat_id: str | None = None,
        content_type: str | None = None,
        error_code: str | None = None,
        latency_ms: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        details_payload = json.dumps(details or {}, ensure_ascii=False)
        if self._parsed.backend == "sqlite":
            assert self._parsed.sqlite_path is not None
            async with aiosqlite.connect(self._parsed.sqlite_path) as conn:
                await conn.execute(
                    """
                    INSERT INTO bot_events(
                      event_type, user_id, chat_id, content_type, status, error_code, latency_ms, details_json
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (event_type, user_id, chat_id, content_type, status, error_code, latency_ms, details_payload),
                )
                await conn.commit()
            return

        assert self._mysql_pool is not None
        async with self._mysql_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO bot_events(
                      event_type, user_id, chat_id, content_type, status, error_code, latency_ms, details_json
                    ) VALUES(%s, %s, %s, %s, %s, %s, %s, CAST(%s AS JSON))
                    """,
                    (event_type, user_id, chat_id, content_type, status, error_code, latency_ms, details_payload),
                )

    async def save_image_generation(
        self,
        *,
        generation_id: str,
        user_id: str,
        chat_id: str,
        original_prompt: str,
        enhanced_prompt: str,
        revised_prompt: str | None,
        model: str | None,
        provider: str | None,
        image_size: str | None,
        image_quality: str | None,
        image_seed: int | None,
    ) -> None:
        if self._parsed.backend == "sqlite":
            assert self._parsed.sqlite_path is not None
            async with aiosqlite.connect(self._parsed.sqlite_path) as conn:
                await conn.execute(
                    """
                    INSERT INTO image_generations(
                      generation_id, user_id, chat_id, original_prompt, enhanced_prompt, revised_prompt,
                      model, provider, image_size, image_quality, image_seed
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        generation_id,
                        user_id,
                        chat_id,
                        original_prompt,
                        enhanced_prompt,
                        revised_prompt,
                        model,
                        provider,
                        image_size,
                        image_quality,
                        image_seed,
                    ),
                )
                await conn.commit()
            return

        assert self._mysql_pool is not None
        async with self._mysql_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO image_generations(
                      generation_id, user_id, chat_id, original_prompt, enhanced_prompt, revised_prompt,
                      model, provider, image_size, image_quality, image_seed
                    ) VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        generation_id,
                        user_id,
                        chat_id,
                        original_prompt,
                        enhanced_prompt,
                        revised_prompt,
                        model,
                        provider,
                        image_size,
                        image_quality,
                        image_seed,
                    ),
                )

    async def get_image_generation(self, generation_id: str, user_id: str) -> dict[str, Any] | None:
        if self._parsed.backend == "sqlite":
            assert self._parsed.sqlite_path is not None
            async with aiosqlite.connect(self._parsed.sqlite_path) as conn:
                row = await (
                    await conn.execute(
                        """
                        SELECT generation_id, user_id, chat_id, original_prompt, enhanced_prompt, revised_prompt,
                               model, provider, image_size, image_quality, image_seed, created_at
                        FROM image_generations
                        WHERE generation_id = ? AND user_id = ?
                        LIMIT 1
                        """,
                        (generation_id, user_id),
                    )
                ).fetchone()
            if not row:
                return None
            return {
                "generation_id": str(row[0]),
                "user_id": str(row[1]),
                "chat_id": str(row[2]),
                "original_prompt": str(row[3]),
                "enhanced_prompt": str(row[4]),
                "revised_prompt": str(row[5] or ""),
                "model": str(row[6] or ""),
                "provider": str(row[7] or ""),
                "image_size": str(row[8] or ""),
                "image_quality": str(row[9] or ""),
                "image_seed": int(row[10]) if row[10] is not None else None,
                "created_at": str(row[11] or ""),
            }

        assert self._mysql_pool is not None
        async with self._mysql_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT generation_id, user_id, chat_id, original_prompt, enhanced_prompt, revised_prompt,
                           model, provider, image_size, image_quality, image_seed, created_at
                    FROM image_generations
                    WHERE generation_id = %s AND user_id = %s
                    LIMIT 1
                    """,
                    (generation_id, user_id),
                )
                row = await cur.fetchone()
        if not row:
            return None
        return {
            "generation_id": str(row[0]),
            "user_id": str(row[1]),
            "chat_id": str(row[2]),
            "original_prompt": str(row[3]),
            "enhanced_prompt": str(row[4]),
            "revised_prompt": str(row[5] or ""),
            "model": str(row[6] or ""),
            "provider": str(row[7] or ""),
            "image_size": str(row[8] or ""),
            "image_quality": str(row[9] or ""),
            "image_seed": int(row[10]) if row[10] is not None else None,
            "created_at": str(row[11] or ""),
        }

    async def log_user_feedback(
        self,
        *,
        user_id: str,
        chat_id: str,
        target_type: str,
        target_id: str,
        feedback_type: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        details_payload = json.dumps(details or {}, ensure_ascii=False)
        if self._parsed.backend == "sqlite":
            assert self._parsed.sqlite_path is not None
            async with aiosqlite.connect(self._parsed.sqlite_path) as conn:
                await conn.execute(
                    """
                    INSERT INTO user_feedback(
                      user_id, chat_id, target_type, target_id, feedback_type, details_json
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, chat_id, target_type, target_id, feedback_type, details_payload),
                )
                await conn.commit()
            return

        assert self._mysql_pool is not None
        async with self._mysql_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO user_feedback(
                      user_id, chat_id, target_type, target_id, feedback_type, details_json
                    ) VALUES(%s, %s, %s, %s, %s, CAST(%s AS JSON))
                    """,
                    (user_id, chat_id, target_type, target_id, feedback_type, details_payload),
                )

    async def save_voice_transcription(
        self,
        *,
        request_id: str,
        user_id: str,
        chat_id: str,
        mode: str,
        topic: str | None,
        raw_transcript: str,
        cleaned_transcript: str,
        analysis_reply: str | None = None,
    ) -> None:
        if self._parsed.backend == "sqlite":
            assert self._parsed.sqlite_path is not None
            async with aiosqlite.connect(self._parsed.sqlite_path) as conn:
                await conn.execute(
                    """
                    INSERT INTO voice_transcriptions(
                      request_id, user_id, chat_id, mode, topic, raw_transcript, cleaned_transcript, analysis_reply
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request_id,
                        user_id,
                        chat_id,
                        mode,
                        topic,
                        raw_transcript,
                        cleaned_transcript,
                        analysis_reply,
                    ),
                )
                await conn.commit()
            return

        assert self._mysql_pool is not None
        async with self._mysql_pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO voice_transcriptions(
                      request_id, user_id, chat_id, mode, topic, raw_transcript, cleaned_transcript, analysis_reply
                    ) VALUES(%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        request_id,
                        user_id,
                        chat_id,
                        mode,
                        topic,
                        raw_transcript,
                        cleaned_transcript,
                        analysis_reply,
                    ),
                )

    async def analytics_snapshot(self, since_hours: int = 24) -> dict[str, Any]:
        since_hours = max(1, int(since_hours))
        if self._parsed.backend == "sqlite":
            assert self._parsed.sqlite_path is not None
            async with aiosqlite.connect(self._parsed.sqlite_path) as conn:
                user_row = await (
                    await conn.execute(
                        "SELECT COUNT(*) FROM users WHERE last_seen_at >= datetime('now', ?)",
                        (f"-{since_hours} hours",),
                    )
                ).fetchone()
                event_rows = await (
                    await conn.execute(
                        """
                        SELECT event_type, status, COUNT(*) AS cnt, AVG(COALESCE(latency_ms, 0)) AS avg_latency
                        FROM bot_events
                        WHERE created_at >= datetime('now', ?)
                        GROUP BY event_type, status
                        ORDER BY cnt DESC
                        """,
                        (f"-{since_hours} hours",),
                    )
                ).fetchall()
                req_rows = await (
                    await conn.execute(
                        """
                        SELECT model, COUNT(*) AS total, SUM(CASE WHEN error_text IS NULL THEN 1 ELSE 0 END) AS ok_count
                        FROM ai_requests
                        WHERE created_at >= datetime('now', ?)
                        GROUP BY model
                        ORDER BY total DESC
                        LIMIT 20
                        """,
                        (f"-{since_hours} hours",),
                    )
                ).fetchall()
        else:
            assert self._mysql_pool is not None
            async with self._mysql_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT COUNT(*) FROM users WHERE last_seen_at >= (UTC_TIMESTAMP() - INTERVAL %s HOUR)",
                        (since_hours,),
                    )
                    user_row = await cur.fetchone()
                    await cur.execute(
                        """
                        SELECT event_type, status, COUNT(*) AS cnt, AVG(COALESCE(latency_ms, 0)) AS avg_latency
                        FROM bot_events
                        WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL %s HOUR)
                        GROUP BY event_type, status
                        ORDER BY cnt DESC
                        """,
                        (since_hours,),
                    )
                    event_rows = await cur.fetchall()
                    await cur.execute(
                        """
                        SELECT model, COUNT(*) AS total, SUM(CASE WHEN error_text IS NULL THEN 1 ELSE 0 END) AS ok_count
                        FROM ai_requests
                        WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL %s HOUR)
                        GROUP BY model
                        ORDER BY total DESC
                        LIMIT 20
                        """,
                        (since_hours,),
                    )
                    req_rows = await cur.fetchall()

        return {
            "window_hours": since_hours,
            "active_users": int((user_row or [0])[0]),
            "events": [
                {
                    "event_type": str(row[0]),
                    "status": str(row[1]),
                    "count": int(row[2] or 0),
                    "avg_latency_ms": float(row[3] or 0.0),
                }
                for row in (event_rows or [])
            ],
            "ai_requests_by_model": [
                {
                    "model": str(row[0]),
                    "count": int(row[1] or 0),
                    "ok_count": int(row[2] or 0),
                }
                for row in (req_rows or [])
            ],
        }

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
