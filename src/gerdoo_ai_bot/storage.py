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

    async def admin_overview(self, since_minutes: int = 1440) -> dict[str, Any]:
        since_minutes = max(1, int(since_minutes))
        latency_values: list[float] = []
        if self._parsed.backend == "sqlite":
            assert self._parsed.sqlite_path is not None
            window = f"-{since_minutes} minutes"
            async with aiosqlite.connect(self._parsed.sqlite_path) as conn:
                total_users_row = await (await conn.execute("SELECT COUNT(*) FROM users")).fetchone()
                active_users_row = await (
                    await conn.execute(
                        "SELECT COUNT(*) FROM users WHERE last_seen_at >= datetime('now', ?)",
                        (window,),
                    )
                ).fetchone()
                new_today_row = await (
                    await conn.execute(
                        "SELECT COUNT(*) FROM users WHERE DATE(first_seen_at) = DATE('now')"
                    )
                ).fetchone()
                total_requests_row = await (await conn.execute("SELECT COUNT(*) FROM ai_requests")).fetchone()
                requests_window_row = await (
                    await conn.execute(
                        "SELECT COUNT(*) FROM ai_requests WHERE created_at >= datetime('now', ?)",
                        (window,),
                    )
                ).fetchone()
                requests_ok_row = await (
                    await conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM ai_requests
                        WHERE created_at >= datetime('now', ?)
                          AND (error_text IS NULL OR error_text = '')
                        """,
                        (window,),
                    )
                ).fetchone()
                events_window_row = await (
                    await conn.execute(
                        "SELECT COUNT(*) FROM bot_events WHERE created_at >= datetime('now', ?)",
                        (window,),
                    )
                ).fetchone()
                events_failed_row = await (
                    await conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM bot_events
                        WHERE created_at >= datetime('now', ?)
                          AND status <> 'ok'
                        """,
                        (window,),
                    )
                ).fetchone()
                feature_rows = await (
                    await conn.execute(
                        """
                        SELECT event_type, status, COUNT(*) AS cnt, AVG(COALESCE(latency_ms, 0)) AS avg_latency_ms
                        FROM bot_events
                        WHERE created_at >= datetime('now', ?)
                        GROUP BY event_type, status
                        ORDER BY cnt DESC
                        """,
                        (window,),
                    )
                ).fetchall()
                content_rows = await (
                    await conn.execute(
                        """
                        SELECT COALESCE(content_type, 'UNKNOWN') AS content_type, COUNT(*) AS cnt
                        FROM bot_events
                        WHERE created_at >= datetime('now', ?)
                        GROUP BY COALESCE(content_type, 'UNKNOWN')
                        ORDER BY cnt DESC
                        """,
                        (window,),
                    )
                ).fetchall()
                error_rows = await (
                    await conn.execute(
                        """
                        SELECT COALESCE(error_code, 'unknown') AS error_code, COUNT(*) AS cnt
                        FROM bot_events
                        WHERE created_at >= datetime('now', ?)
                          AND status <> 'ok'
                        GROUP BY COALESCE(error_code, 'unknown')
                        ORDER BY cnt DESC
                        LIMIT 20
                        """,
                        (window,),
                    )
                ).fetchall()
                latency_rows = await (
                    await conn.execute(
                        """
                        SELECT latency_ms
                        FROM bot_events
                        WHERE created_at >= datetime('now', ?)
                          AND latency_ms IS NOT NULL
                        ORDER BY latency_ms ASC
                        """,
                        (window,),
                    )
                ).fetchall()
                hourly_rows = await (
                    await conn.execute(
                        """
                        SELECT strftime('%Y-%m-%d %H:00', created_at) AS hour_slot, COUNT(*) AS cnt
                        FROM bot_events
                        WHERE created_at >= datetime('now', '-24 hours')
                        GROUP BY hour_slot
                        ORDER BY hour_slot ASC
                        """
                    )
                ).fetchall()
        else:
            assert self._mysql_pool is not None
            async with self._mysql_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT COUNT(*) FROM users")
                    total_users_row = await cur.fetchone()
                    await cur.execute(
                        "SELECT COUNT(*) FROM users WHERE last_seen_at >= (UTC_TIMESTAMP() - INTERVAL %s MINUTE)",
                        (since_minutes,),
                    )
                    active_users_row = await cur.fetchone()
                    await cur.execute("SELECT COUNT(*) FROM users WHERE DATE(first_seen_at) = UTC_DATE()")
                    new_today_row = await cur.fetchone()
                    await cur.execute("SELECT COUNT(*) FROM ai_requests")
                    total_requests_row = await cur.fetchone()
                    await cur.execute(
                        "SELECT COUNT(*) FROM ai_requests WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL %s MINUTE)",
                        (since_minutes,),
                    )
                    requests_window_row = await cur.fetchone()
                    await cur.execute(
                        """
                        SELECT COUNT(*)
                        FROM ai_requests
                        WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL %s MINUTE)
                          AND (error_text IS NULL OR error_text = '')
                        """,
                        (since_minutes,),
                    )
                    requests_ok_row = await cur.fetchone()
                    await cur.execute(
                        "SELECT COUNT(*) FROM bot_events WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL %s MINUTE)",
                        (since_minutes,),
                    )
                    events_window_row = await cur.fetchone()
                    await cur.execute(
                        """
                        SELECT COUNT(*)
                        FROM bot_events
                        WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL %s MINUTE)
                          AND status <> 'ok'
                        """,
                        (since_minutes,),
                    )
                    events_failed_row = await cur.fetchone()
                    await cur.execute(
                        """
                        SELECT event_type, status, COUNT(*) AS cnt, AVG(COALESCE(latency_ms, 0)) AS avg_latency_ms
                        FROM bot_events
                        WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL %s MINUTE)
                        GROUP BY event_type, status
                        ORDER BY cnt DESC
                        """,
                        (since_minutes,),
                    )
                    feature_rows = await cur.fetchall()
                    await cur.execute(
                        """
                        SELECT COALESCE(content_type, 'UNKNOWN') AS content_type, COUNT(*) AS cnt
                        FROM bot_events
                        WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL %s MINUTE)
                        GROUP BY COALESCE(content_type, 'UNKNOWN')
                        ORDER BY cnt DESC
                        """,
                        (since_minutes,),
                    )
                    content_rows = await cur.fetchall()
                    await cur.execute(
                        """
                        SELECT COALESCE(error_code, 'unknown') AS error_code, COUNT(*) AS cnt
                        FROM bot_events
                        WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL %s MINUTE)
                          AND status <> 'ok'
                        GROUP BY COALESCE(error_code, 'unknown')
                        ORDER BY cnt DESC
                        LIMIT 20
                        """,
                        (since_minutes,),
                    )
                    error_rows = await cur.fetchall()
                    await cur.execute(
                        """
                        SELECT latency_ms
                        FROM bot_events
                        WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL %s MINUTE)
                          AND latency_ms IS NOT NULL
                        ORDER BY latency_ms ASC
                        """,
                        (since_minutes,),
                    )
                    latency_rows = await cur.fetchall()
                    await cur.execute(
                        """
                        SELECT DATE_FORMAT(created_at, '%Y-%m-%d %H:00') AS hour_slot, COUNT(*) AS cnt
                        FROM bot_events
                        WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL 24 HOUR)
                        GROUP BY hour_slot
                        ORDER BY hour_slot ASC
                        """
                    )
                    hourly_rows = await cur.fetchall()

        for row in (latency_rows or []):
            try:
                latency_values.append(float(row[0]))
            except Exception:  # noqa: BLE001
                continue

        p95_latency = 0.0
        if latency_values:
            idx = int(round(0.95 * (len(latency_values) - 1)))
            idx = max(0, min(len(latency_values) - 1, idx))
            p95_latency = float(latency_values[idx])

        requests_window = int((requests_window_row or [0])[0])
        requests_ok = int((requests_ok_row or [0])[0])
        requests_failed = max(0, requests_window - requests_ok)
        success_rate = float(requests_ok / requests_window) if requests_window > 0 else 0.0

        return {
            "window_minutes": since_minutes,
            "users": {
                "total": int((total_users_row or [0])[0]),
                "active_window": int((active_users_row or [0])[0]),
                "new_today": int((new_today_row or [0])[0]),
            },
            "requests": {
                "total": int((total_requests_row or [0])[0]),
                "window_total": requests_window,
                "window_ok": requests_ok,
                "window_failed": requests_failed,
                "window_success_rate": success_rate,
            },
            "events": {
                "window_total": int((events_window_row or [0])[0]),
                "window_failed": int((events_failed_row or [0])[0]),
            },
            "latency": {
                "window_avg_ms": float(sum(latency_values) / len(latency_values)) if latency_values else 0.0,
                "window_p95_ms": p95_latency,
            },
            "feature_usage": [
                {
                    "event_type": str(row[0]),
                    "status": str(row[1]),
                    "count": int(row[2] or 0),
                    "avg_latency_ms": float(row[3] or 0.0),
                }
                for row in (feature_rows or [])
            ],
            "content_usage": [
                {"content_type": str(row[0]), "count": int(row[1] or 0)}
                for row in (content_rows or [])
            ],
            "errors": [
                {"error_code": str(row[0]), "count": int(row[1] or 0)}
                for row in (error_rows or [])
            ],
            "throughput_24h": [
                {"hour": str(row[0]), "count": int(row[1] or 0)}
                for row in (hourly_rows or [])
            ],
        }

    async def admin_daily_series(self, days: int = 30) -> list[dict[str, Any]]:
        days = max(1, min(120, int(days)))
        if self._parsed.backend == "sqlite":
            assert self._parsed.sqlite_path is not None
            day_window = f"-{days - 1} days"
            async with aiosqlite.connect(self._parsed.sqlite_path) as conn:
                req_rows = await (
                    await conn.execute(
                        """
                        SELECT DATE(created_at) AS day, COUNT(*) AS cnt
                        FROM ai_requests
                        WHERE created_at >= datetime('now', ?)
                        GROUP BY DATE(created_at)
                        """,
                        (day_window,),
                    )
                ).fetchall()
                user_new_rows = await (
                    await conn.execute(
                        """
                        SELECT DATE(first_seen_at) AS day, COUNT(*) AS cnt
                        FROM users
                        WHERE first_seen_at >= datetime('now', ?)
                        GROUP BY DATE(first_seen_at)
                        """,
                        (day_window,),
                    )
                ).fetchall()
                active_rows = await (
                    await conn.execute(
                        """
                        SELECT DATE(created_at) AS day, COUNT(DISTINCT user_id) AS cnt
                        FROM bot_events
                        WHERE created_at >= datetime('now', ?)
                          AND user_id IS NOT NULL
                        GROUP BY DATE(created_at)
                        """,
                        (day_window,),
                    )
                ).fetchall()
                event_rows = await (
                    await conn.execute(
                        """
                        SELECT DATE(created_at) AS day,
                               COUNT(*) AS total_events,
                               SUM(CASE WHEN status <> 'ok' THEN 1 ELSE 0 END) AS failed_events
                        FROM bot_events
                        WHERE created_at >= datetime('now', ?)
                        GROUP BY DATE(created_at)
                        """,
                        (day_window,),
                    )
                ).fetchall()
                image_rows = await (
                    await conn.execute(
                        """
                        SELECT DATE(created_at) AS day, COUNT(*) AS cnt
                        FROM image_generations
                        WHERE created_at >= datetime('now', ?)
                        GROUP BY DATE(created_at)
                        """,
                        (day_window,),
                    )
                ).fetchall()
                voice_rows = await (
                    await conn.execute(
                        """
                        SELECT DATE(created_at) AS day, COUNT(*) AS cnt
                        FROM voice_transcriptions
                        WHERE created_at >= datetime('now', ?)
                        GROUP BY DATE(created_at)
                        """,
                        (day_window,),
                    )
                ).fetchall()
        else:
            assert self._mysql_pool is not None
            async with self._mysql_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT DATE(created_at) AS day, COUNT(*) AS cnt
                        FROM ai_requests
                        WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL %s DAY)
                        GROUP BY DATE(created_at)
                        """,
                        (days,),
                    )
                    req_rows = await cur.fetchall()
                    await cur.execute(
                        """
                        SELECT DATE(first_seen_at) AS day, COUNT(*) AS cnt
                        FROM users
                        WHERE first_seen_at >= (UTC_TIMESTAMP() - INTERVAL %s DAY)
                        GROUP BY DATE(first_seen_at)
                        """,
                        (days,),
                    )
                    user_new_rows = await cur.fetchall()
                    await cur.execute(
                        """
                        SELECT DATE(created_at) AS day, COUNT(DISTINCT user_id) AS cnt
                        FROM bot_events
                        WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL %s DAY)
                          AND user_id IS NOT NULL
                        GROUP BY DATE(created_at)
                        """,
                        (days,),
                    )
                    active_rows = await cur.fetchall()
                    await cur.execute(
                        """
                        SELECT DATE(created_at) AS day,
                               COUNT(*) AS total_events,
                               SUM(CASE WHEN status <> 'ok' THEN 1 ELSE 0 END) AS failed_events
                        FROM bot_events
                        WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL %s DAY)
                        GROUP BY DATE(created_at)
                        """,
                        (days,),
                    )
                    event_rows = await cur.fetchall()
                    await cur.execute(
                        """
                        SELECT DATE(created_at) AS day, COUNT(*) AS cnt
                        FROM image_generations
                        WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL %s DAY)
                        GROUP BY DATE(created_at)
                        """,
                        (days,),
                    )
                    image_rows = await cur.fetchall()
                    await cur.execute(
                        """
                        SELECT DATE(created_at) AS day, COUNT(*) AS cnt
                        FROM voice_transcriptions
                        WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL %s DAY)
                        GROUP BY DATE(created_at)
                        """,
                        (days,),
                    )
                    voice_rows = await cur.fetchall()

        out: dict[str, dict[str, Any]] = {}

        def ensure_day(day_key: str) -> dict[str, Any]:
            if day_key not in out:
                out[day_key] = {
                    "day": day_key,
                    "new_users": 0,
                    "active_users": 0,
                    "ai_requests": 0,
                    "events_total": 0,
                    "events_failed": 0,
                    "images_generated": 0,
                    "voice_transcriptions": 0,
                }
            return out[day_key]

        for row in (req_rows or []):
            item = ensure_day(str(row[0]))
            item["ai_requests"] = int(row[1] or 0)
        for row in (user_new_rows or []):
            item = ensure_day(str(row[0]))
            item["new_users"] = int(row[1] or 0)
        for row in (active_rows or []):
            item = ensure_day(str(row[0]))
            item["active_users"] = int(row[1] or 0)
        for row in (event_rows or []):
            item = ensure_day(str(row[0]))
            item["events_total"] = int(row[1] or 0)
            item["events_failed"] = int(row[2] or 0)
        for row in (image_rows or []):
            item = ensure_day(str(row[0]))
            item["images_generated"] = int(row[1] or 0)
        for row in (voice_rows or []):
            item = ensure_day(str(row[0]))
            item["voice_transcriptions"] = int(row[1] or 0)

        return [out[key] for key in sorted(out.keys())]

    async def admin_model_usage(self, since_minutes: int = 1440) -> dict[str, list[dict[str, Any]]]:
        since_minutes = max(1, int(since_minutes))
        if self._parsed.backend == "sqlite":
            assert self._parsed.sqlite_path is not None
            window = f"-{since_minutes} minutes"
            async with aiosqlite.connect(self._parsed.sqlite_path) as conn:
                chat_rows = await (
                    await conn.execute(
                        """
                        SELECT model,
                               COUNT(*) AS total,
                               SUM(CASE WHEN error_text IS NULL OR error_text = '' THEN 1 ELSE 0 END) AS ok_count,
                               COUNT(DISTINCT user_id) AS unique_users
                        FROM ai_requests
                        WHERE created_at >= datetime('now', ?)
                        GROUP BY model
                        ORDER BY total DESC
                        """,
                        (window,),
                    )
                ).fetchall()
                image_rows = await (
                    await conn.execute(
                        """
                        SELECT COALESCE(model, 'unknown') AS model,
                               COALESCE(provider, 'unknown') AS provider,
                               COUNT(*) AS total,
                               COUNT(DISTINCT user_id) AS unique_users
                        FROM image_generations
                        WHERE created_at >= datetime('now', ?)
                        GROUP BY COALESCE(model, 'unknown'), COALESCE(provider, 'unknown')
                        ORDER BY total DESC
                        """,
                        (window,),
                    )
                ).fetchall()
                voice_rows = await (
                    await conn.execute(
                        """
                        SELECT mode, COUNT(*) AS total, COUNT(DISTINCT user_id) AS unique_users
                        FROM voice_transcriptions
                        WHERE created_at >= datetime('now', ?)
                        GROUP BY mode
                        ORDER BY total DESC
                        """,
                        (window,),
                    )
                ).fetchall()
        else:
            assert self._mysql_pool is not None
            async with self._mysql_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT model,
                               COUNT(*) AS total,
                               SUM(CASE WHEN error_text IS NULL OR error_text = '' THEN 1 ELSE 0 END) AS ok_count,
                               COUNT(DISTINCT user_id) AS unique_users
                        FROM ai_requests
                        WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL %s MINUTE)
                        GROUP BY model
                        ORDER BY total DESC
                        """,
                        (since_minutes,),
                    )
                    chat_rows = await cur.fetchall()
                    await cur.execute(
                        """
                        SELECT COALESCE(model, 'unknown') AS model,
                               COALESCE(provider, 'unknown') AS provider,
                               COUNT(*) AS total,
                               COUNT(DISTINCT user_id) AS unique_users
                        FROM image_generations
                        WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL %s MINUTE)
                        GROUP BY COALESCE(model, 'unknown'), COALESCE(provider, 'unknown')
                        ORDER BY total DESC
                        """,
                        (since_minutes,),
                    )
                    image_rows = await cur.fetchall()
                    await cur.execute(
                        """
                        SELECT mode, COUNT(*) AS total, COUNT(DISTINCT user_id) AS unique_users
                        FROM voice_transcriptions
                        WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL %s MINUTE)
                        GROUP BY mode
                        ORDER BY total DESC
                        """,
                        (since_minutes,),
                    )
                    voice_rows = await cur.fetchall()

        return {
            "chat": [
                {
                    "model": str(row[0]),
                    "count": int(row[1] or 0),
                    "ok_count": int(row[2] or 0),
                    "unique_users": int(row[3] or 0),
                }
                for row in (chat_rows or [])
            ],
            "image_generation": [
                {
                    "model": str(row[0]),
                    "provider": str(row[1]),
                    "count": int(row[2] or 0),
                    "unique_users": int(row[3] or 0),
                }
                for row in (image_rows or [])
            ],
            "voice": [
                {
                    "mode": str(row[0]),
                    "count": int(row[1] or 0),
                    "unique_users": int(row[2] or 0),
                }
                for row in (voice_rows or [])
            ],
        }

    async def admin_top_users(self, since_minutes: int = 1440, limit: int = 50) -> list[dict[str, Any]]:
        since_minutes = max(1, int(since_minutes))
        limit = max(1, min(500, int(limit)))
        if self._parsed.backend == "sqlite":
            assert self._parsed.sqlite_path is not None
            window = f"-{since_minutes} minutes"
            async with aiosqlite.connect(self._parsed.sqlite_path) as conn:
                rows = await (
                    await conn.execute(
                        """
                        SELECT
                          u.user_id,
                          u.chat_id,
                          COALESCE(u.username, '') AS username,
                          u.display_name,
                          u.last_seen_at,
                          COALESCE(ev.total_events, 0) AS events_total,
                          COALESCE(ev.failed_events, 0) AS failed_events,
                          COALESCE(req.total_requests, 0) AS ai_requests,
                          COALESCE(img.total_images, 0) AS images_generated,
                          COALESCE(vt.total_voice, 0) AS voice_transcriptions,
                          COALESCE(fb.total_feedback, 0) AS feedback_count
                        FROM users u
                        LEFT JOIN (
                          SELECT user_id,
                                 COUNT(*) AS total_events,
                                 SUM(CASE WHEN status <> 'ok' THEN 1 ELSE 0 END) AS failed_events
                          FROM bot_events
                          WHERE created_at >= datetime('now', ?)
                          GROUP BY user_id
                        ) ev ON ev.user_id = u.user_id
                        LEFT JOIN (
                          SELECT user_id, COUNT(*) AS total_requests
                          FROM ai_requests
                          WHERE created_at >= datetime('now', ?)
                          GROUP BY user_id
                        ) req ON req.user_id = u.user_id
                        LEFT JOIN (
                          SELECT user_id, COUNT(*) AS total_images
                          FROM image_generations
                          WHERE created_at >= datetime('now', ?)
                          GROUP BY user_id
                        ) img ON img.user_id = u.user_id
                        LEFT JOIN (
                          SELECT user_id, COUNT(*) AS total_voice
                          FROM voice_transcriptions
                          WHERE created_at >= datetime('now', ?)
                          GROUP BY user_id
                        ) vt ON vt.user_id = u.user_id
                        LEFT JOIN (
                          SELECT user_id, COUNT(*) AS total_feedback
                          FROM user_feedback
                          WHERE created_at >= datetime('now', ?)
                          GROUP BY user_id
                        ) fb ON fb.user_id = u.user_id
                        WHERE u.last_seen_at >= datetime('now', ?)
                           OR COALESCE(ev.total_events, 0) > 0
                           OR COALESCE(req.total_requests, 0) > 0
                           OR COALESCE(img.total_images, 0) > 0
                           OR COALESCE(vt.total_voice, 0) > 0
                        ORDER BY (COALESCE(req.total_requests, 0) + COALESCE(img.total_images, 0) + COALESCE(vt.total_voice, 0)) DESC,
                                 COALESCE(ev.total_events, 0) DESC
                        LIMIT ?
                        """,
                        (window, window, window, window, window, window, limit),
                    )
                ).fetchall()
        else:
            assert self._mysql_pool is not None
            async with self._mysql_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT
                          u.user_id,
                          u.chat_id,
                          COALESCE(u.username, '') AS username,
                          u.display_name,
                          u.last_seen_at,
                          COALESCE(ev.total_events, 0) AS events_total,
                          COALESCE(ev.failed_events, 0) AS failed_events,
                          COALESCE(req.total_requests, 0) AS ai_requests,
                          COALESCE(img.total_images, 0) AS images_generated,
                          COALESCE(vt.total_voice, 0) AS voice_transcriptions,
                          COALESCE(fb.total_feedback, 0) AS feedback_count
                        FROM users u
                        LEFT JOIN (
                          SELECT user_id,
                                 COUNT(*) AS total_events,
                                 SUM(CASE WHEN status <> 'ok' THEN 1 ELSE 0 END) AS failed_events
                          FROM bot_events
                          WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL %s MINUTE)
                          GROUP BY user_id
                        ) ev ON ev.user_id = u.user_id
                        LEFT JOIN (
                          SELECT user_id, COUNT(*) AS total_requests
                          FROM ai_requests
                          WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL %s MINUTE)
                          GROUP BY user_id
                        ) req ON req.user_id = u.user_id
                        LEFT JOIN (
                          SELECT user_id, COUNT(*) AS total_images
                          FROM image_generations
                          WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL %s MINUTE)
                          GROUP BY user_id
                        ) img ON img.user_id = u.user_id
                        LEFT JOIN (
                          SELECT user_id, COUNT(*) AS total_voice
                          FROM voice_transcriptions
                          WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL %s MINUTE)
                          GROUP BY user_id
                        ) vt ON vt.user_id = u.user_id
                        LEFT JOIN (
                          SELECT user_id, COUNT(*) AS total_feedback
                          FROM user_feedback
                          WHERE created_at >= (UTC_TIMESTAMP() - INTERVAL %s MINUTE)
                          GROUP BY user_id
                        ) fb ON fb.user_id = u.user_id
                        WHERE u.last_seen_at >= (UTC_TIMESTAMP() - INTERVAL %s MINUTE)
                           OR COALESCE(ev.total_events, 0) > 0
                           OR COALESCE(req.total_requests, 0) > 0
                           OR COALESCE(img.total_images, 0) > 0
                           OR COALESCE(vt.total_voice, 0) > 0
                        ORDER BY (COALESCE(req.total_requests, 0) + COALESCE(img.total_images, 0) + COALESCE(vt.total_voice, 0)) DESC,
                                 COALESCE(ev.total_events, 0) DESC
                        LIMIT %s
                        """,
                        (
                            since_minutes,
                            since_minutes,
                            since_minutes,
                            since_minutes,
                            since_minutes,
                            since_minutes,
                            limit,
                        ),
                    )
                    rows = await cur.fetchall()

        return [
            {
                "user_id": str(row[0]),
                "chat_id": str(row[1]),
                "username": str(row[2] or ""),
                "display_name": str(row[3] or ""),
                "last_seen_at": str(row[4] or ""),
                "events_total": int(row[5] or 0),
                "failed_events": int(row[6] or 0),
                "ai_requests": int(row[7] or 0),
                "images_generated": int(row[8] or 0),
                "voice_transcriptions": int(row[9] or 0),
                "feedback_count": int(row[10] or 0),
            }
            for row in (rows or [])
        ]

    async def admin_recent_events(
        self,
        *,
        since_minutes: int = 1440,
        limit: int = 300,
        event_type: str | None = None,
        status: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        since_minutes = max(1, int(since_minutes))
        limit = max(1, min(2000, int(limit)))
        event_type = (event_type or "").strip()
        status = (status or "").strip()
        user_id = (user_id or "").strip()
        where_clauses: list[str] = []
        params: list[Any] = []

        if self._parsed.backend == "sqlite":
            where_clauses.append("created_at >= datetime('now', ?)")
            params.append(f"-{since_minutes} minutes")
            if event_type:
                where_clauses.append("event_type = ?")
                params.append(event_type)
            if status:
                where_clauses.append("status = ?")
                params.append(status)
            if user_id:
                where_clauses.append("user_id = ?")
                params.append(user_id)
            where_sql = " AND ".join(where_clauses)
            query = (
                """
                SELECT id, event_type, user_id, chat_id, content_type, status, error_code, latency_ms, details_json, created_at
                FROM bot_events
                WHERE """
                + where_sql
                + """
                ORDER BY id DESC
                LIMIT ?
                """
            )
            params.append(limit)
            assert self._parsed.sqlite_path is not None
            async with aiosqlite.connect(self._parsed.sqlite_path) as conn:
                rows = await (await conn.execute(query, tuple(params))).fetchall()
        else:
            where_clauses.append("created_at >= (UTC_TIMESTAMP() - INTERVAL %s MINUTE)")
            params.append(since_minutes)
            if event_type:
                where_clauses.append("event_type = %s")
                params.append(event_type)
            if status:
                where_clauses.append("status = %s")
                params.append(status)
            if user_id:
                where_clauses.append("user_id = %s")
                params.append(user_id)
            where_sql = " AND ".join(where_clauses)
            query = (
                """
                SELECT id, event_type, user_id, chat_id, content_type, status, error_code, latency_ms, details_json, created_at
                FROM bot_events
                WHERE """
                + where_sql
                + """
                ORDER BY id DESC
                LIMIT %s
                """
            )
            params.append(limit)
            assert self._mysql_pool is not None
            async with self._mysql_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(query, tuple(params))
                    rows = await cur.fetchall()

        out: list[dict[str, Any]] = []
        for row in (rows or []):
            details_json = row[8]
            details: dict[str, Any]
            if isinstance(details_json, dict):
                details = details_json
            else:
                try:
                    details = json.loads(str(details_json or "{}"))
                except Exception:  # noqa: BLE001
                    details = {"raw": str(details_json or "")}
            out.append(
                {
                    "id": int(row[0]),
                    "event_type": str(row[1]),
                    "user_id": str(row[2] or ""),
                    "chat_id": str(row[3] or ""),
                    "content_type": str(row[4] or ""),
                    "status": str(row[5] or ""),
                    "error_code": str(row[6] or ""),
                    "latency_ms": float(row[7] or 0.0),
                    "details": details,
                    "created_at": str(row[9] or ""),
                }
            )
        return out

    async def admin_feature_timeseries(
        self,
        *,
        since_minutes: int = 10080,
        bucket: str = "hour",
        user_id: str | None = None,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        since_minutes = max(5, int(since_minutes))
        bucket = "day" if str(bucket).strip().lower() == "day" else "hour"
        user_id = (user_id or "").strip()
        event_type = (event_type or "").strip()

        if self._parsed.backend == "sqlite":
            assert self._parsed.sqlite_path is not None
            bucket_expr = "strftime('%Y-%m-%d', created_at)" if bucket == "day" else "strftime('%Y-%m-%d %H:00', created_at)"
            where = ["created_at >= datetime('now', ?)"]
            params: list[Any] = [f"-{since_minutes} minutes"]
            if user_id:
                where.append("user_id = ?")
                params.append(user_id)
            if event_type:
                where.append("event_type = ?")
                params.append(event_type)
            where_sql = " AND ".join(where)
            query = f"""
                SELECT {bucket_expr} AS bucket_key,
                       event_type,
                       COUNT(*) AS total_count,
                       SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_count,
                       SUM(CASE WHEN status <> 'ok' THEN 1 ELSE 0 END) AS failed_count,
                       AVG(COALESCE(latency_ms, 0)) AS avg_latency_ms
                FROM bot_events
                WHERE {where_sql}
                GROUP BY bucket_key, event_type
                ORDER BY bucket_key ASC, event_type ASC
            """
            async with aiosqlite.connect(self._parsed.sqlite_path) as conn:
                rows = await (await conn.execute(query, tuple(params))).fetchall()
        else:
            assert self._mysql_pool is not None
            bucket_expr = "DATE_FORMAT(created_at, '%%Y-%%m-%%d')" if bucket == "day" else "DATE_FORMAT(created_at, '%%Y-%%m-%%d %%H:00')"
            where = ["created_at >= (UTC_TIMESTAMP() - INTERVAL %s MINUTE)"]
            params = [since_minutes]
            if user_id:
                where.append("user_id = %s")
                params.append(user_id)
            if event_type:
                where.append("event_type = %s")
                params.append(event_type)
            where_sql = " AND ".join(where)
            query = f"""
                SELECT {bucket_expr} AS bucket_key,
                       event_type,
                       COUNT(*) AS total_count,
                       SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_count,
                       SUM(CASE WHEN status <> 'ok' THEN 1 ELSE 0 END) AS failed_count,
                       AVG(COALESCE(latency_ms, 0)) AS avg_latency_ms
                FROM bot_events
                WHERE {where_sql}
                GROUP BY bucket_key, event_type
                ORDER BY bucket_key ASC, event_type ASC
            """
            async with self._mysql_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(query, tuple(params))
                    rows = await cur.fetchall()

        return [
            {
                "bucket": str(row[0]),
                "event_type": str(row[1] or ""),
                "count": int(row[2] or 0),
                "ok_count": int(row[3] or 0),
                "failed_count": int(row[4] or 0),
                "avg_latency_ms": float(row[5] or 0.0),
            }
            for row in (rows or [])
        ]

    async def admin_model_timeseries(
        self,
        *,
        since_minutes: int = 10080,
        bucket: str = "hour",
        user_id: str | None = None,
        model: str | None = None,
    ) -> list[dict[str, Any]]:
        since_minutes = max(5, int(since_minutes))
        bucket = "day" if str(bucket).strip().lower() == "day" else "hour"
        user_id = (user_id or "").strip()
        model = (model or "").strip()

        if self._parsed.backend == "sqlite":
            assert self._parsed.sqlite_path is not None
            bucket_expr = "strftime('%Y-%m-%d', created_at)" if bucket == "day" else "strftime('%Y-%m-%d %H:00', created_at)"
            where = ["created_at >= datetime('now', ?)"]
            params: list[Any] = [f"-{since_minutes} minutes"]
            if user_id:
                where.append("user_id = ?")
                params.append(user_id)
            if model:
                where.append("model = ?")
                params.append(model)
            where_sql = " AND ".join(where)
            query = f"""
                SELECT {bucket_expr} AS bucket_key,
                       model,
                       COUNT(*) AS total_count,
                       SUM(CASE WHEN error_text IS NULL OR error_text = '' THEN 1 ELSE 0 END) AS ok_count,
                       SUM(CASE WHEN error_text IS NOT NULL AND error_text <> '' THEN 1 ELSE 0 END) AS failed_count
                FROM ai_requests
                WHERE {where_sql}
                GROUP BY bucket_key, model
                ORDER BY bucket_key ASC, model ASC
            """
            async with aiosqlite.connect(self._parsed.sqlite_path) as conn:
                rows = await (await conn.execute(query, tuple(params))).fetchall()
        else:
            assert self._mysql_pool is not None
            bucket_expr = "DATE_FORMAT(created_at, '%%Y-%%m-%%d')" if bucket == "day" else "DATE_FORMAT(created_at, '%%Y-%%m-%%d %%H:00')"
            where = ["created_at >= (UTC_TIMESTAMP() - INTERVAL %s MINUTE)"]
            params = [since_minutes]
            if user_id:
                where.append("user_id = %s")
                params.append(user_id)
            if model:
                where.append("model = %s")
                params.append(model)
            where_sql = " AND ".join(where)
            query = f"""
                SELECT {bucket_expr} AS bucket_key,
                       model,
                       COUNT(*) AS total_count,
                       SUM(CASE WHEN error_text IS NULL OR error_text = '' THEN 1 ELSE 0 END) AS ok_count,
                       SUM(CASE WHEN error_text IS NOT NULL AND error_text <> '' THEN 1 ELSE 0 END) AS failed_count
                FROM ai_requests
                WHERE {where_sql}
                GROUP BY bucket_key, model
                ORDER BY bucket_key ASC, model ASC
            """
            async with self._mysql_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(query, tuple(params))
                    rows = await cur.fetchall()

        return [
            {
                "bucket": str(row[0]),
                "model": str(row[1] or ""),
                "count": int(row[2] or 0),
                "ok_count": int(row[3] or 0),
                "failed_count": int(row[4] or 0),
            }
            for row in (rows or [])
        ]

    async def admin_user_model_usage(
        self,
        *,
        since_minutes: int = 1440,
        limit: int = 300,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        since_minutes = max(5, int(since_minutes))
        limit = max(1, min(2000, int(limit)))
        user_id = (user_id or "").strip()

        if self._parsed.backend == "sqlite":
            assert self._parsed.sqlite_path is not None
            where = ["r.created_at >= datetime('now', ?)"]
            params: list[Any] = [f"-{since_minutes} minutes"]
            if user_id:
                where.append("r.user_id = ?")
                params.append(user_id)
            where_sql = " AND ".join(where)
            query = f"""
                SELECT
                  r.user_id,
                  COALESCE(u.display_name, r.user_id) AS display_name,
                  COALESCE(u.username, '') AS username,
                  r.model,
                  COUNT(*) AS total_count,
                  SUM(CASE WHEN r.error_text IS NULL OR r.error_text = '' THEN 1 ELSE 0 END) AS ok_count
                FROM ai_requests r
                LEFT JOIN users u ON u.user_id = r.user_id
                WHERE {where_sql}
                GROUP BY r.user_id, u.display_name, u.username, r.model
                ORDER BY total_count DESC
                LIMIT ?
            """
            params.append(limit)
            async with aiosqlite.connect(self._parsed.sqlite_path) as conn:
                rows = await (await conn.execute(query, tuple(params))).fetchall()
        else:
            assert self._mysql_pool is not None
            where = ["r.created_at >= (UTC_TIMESTAMP() - INTERVAL %s MINUTE)"]
            params = [since_minutes]
            if user_id:
                where.append("r.user_id = %s")
                params.append(user_id)
            where_sql = " AND ".join(where)
            query = f"""
                SELECT
                  r.user_id,
                  COALESCE(u.display_name, r.user_id) AS display_name,
                  COALESCE(u.username, '') AS username,
                  r.model,
                  COUNT(*) AS total_count,
                  SUM(CASE WHEN r.error_text IS NULL OR r.error_text = '' THEN 1 ELSE 0 END) AS ok_count
                FROM ai_requests r
                LEFT JOIN users u ON u.user_id = r.user_id
                WHERE {where_sql}
                GROUP BY r.user_id, u.display_name, u.username, r.model
                ORDER BY total_count DESC
                LIMIT %s
            """
            params.append(limit)
            async with self._mysql_pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(query, tuple(params))
                    rows = await cur.fetchall()

        return [
            {
                "user_id": str(row[0] or ""),
                "display_name": str(row[1] or ""),
                "username": str(row[2] or ""),
                "model": str(row[3] or ""),
                "count": int(row[4] or 0),
                "ok_count": int(row[5] or 0),
                "failed_count": max(0, int(row[4] or 0) - int(row[5] or 0)),
            }
            for row in (rows or [])
        ]

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
