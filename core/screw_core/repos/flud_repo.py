"""Repository for informal chat pulse statistics."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from core.screw_core.db.tx import acquire_connection, parse_rowcount


class FludRepo:
    """Storage access for flud message metadata, reactions and digest runs."""

    @staticmethod
    async def upsert_message(**payload: Any) -> int:
        """Insert or update a message metadata row by `(chat_id, message_id)`."""
        async with acquire_connection() as conn:
            return await conn.fetchval(
                """
                INSERT INTO screw.flud_messages (
                    chat_id, message_id, telegram_user_id, user_id, sent_at,
                    message_type, text_len, word_count, text_preview,
                    has_text, has_photo, has_video, has_animation, has_sticker,
                    has_voice, has_document, is_reply, reply_to_message_id,
                    source_author_hash
                )
                VALUES (
                    $1, $2, $3, $4, $5,
                    $6, $7, $8, $9,
                    $10, $11, $12, $13, $14,
                    $15, $16, $17, $18,
                    $19
                )
                ON CONFLICT (chat_id, message_id) DO UPDATE SET
                    telegram_user_id = EXCLUDED.telegram_user_id,
                    user_id = EXCLUDED.user_id,
                    sent_at = EXCLUDED.sent_at,
                    message_type = EXCLUDED.message_type,
                    text_len = EXCLUDED.text_len,
                    word_count = EXCLUDED.word_count,
                    text_preview = EXCLUDED.text_preview,
                    has_text = EXCLUDED.has_text,
                    has_photo = EXCLUDED.has_photo,
                    has_video = EXCLUDED.has_video,
                    has_animation = EXCLUDED.has_animation,
                    has_sticker = EXCLUDED.has_sticker,
                    has_voice = EXCLUDED.has_voice,
                    has_document = EXCLUDED.has_document,
                    is_reply = EXCLUDED.is_reply,
                    reply_to_message_id = EXCLUDED.reply_to_message_id,
                    source_author_hash = EXCLUDED.source_author_hash
                RETURNING id
                """,
                payload["chat_id"],
                payload["message_id"],
                payload.get("telegram_user_id"),
                payload.get("user_id"),
                payload["sent_at"],
                payload["message_type"],
                payload.get("text_len", 0),
                payload.get("word_count", 0),
                payload.get("text_preview"),
                payload.get("has_text", False),
                payload.get("has_photo", False),
                payload.get("has_video", False),
                payload.get("has_animation", False),
                payload.get("has_sticker", False),
                payload.get("has_voice", False),
                payload.get("has_document", False),
                payload.get("is_reply", False),
                payload.get("reply_to_message_id"),
                payload.get("source_author_hash"),
            )

    @staticmethod
    async def upsert_reactions(chat_id: int, message_id: int, reactions: dict[str, int]) -> None:
        """Set authoritative reaction counts for a message."""
        if not reactions:
            return
        async with acquire_connection() as conn:
            await conn.executemany(
                """
                INSERT INTO screw.flud_message_reactions
                    (chat_id, message_id, reaction_type, reaction_count, updated_at)
                VALUES ($1, $2, $3, GREATEST($4, 0), NOW())
                ON CONFLICT (chat_id, message_id, reaction_type) DO UPDATE SET
                    reaction_count = EXCLUDED.reaction_count,
                    updated_at = NOW()
                """,
                [(chat_id, message_id, reaction_type, count) for reaction_type, count in reactions.items()],
            )

    @staticmethod
    async def message_exists(chat_id: int, message_id: int) -> bool:
        """Check whether a message metadata row exists."""
        async with acquire_connection() as conn:
            value = await conn.fetchval(
                """
                SELECT 1
                FROM screw.flud_messages
                WHERE chat_id = $1
                  AND message_id = $2
                """,
                chat_id,
                message_id,
            )
            return bool(value)

    @staticmethod
    async def apply_reaction_delta(chat_id: int, message_id: int, reactions_delta: dict[str, int]) -> None:
        """Apply privacy-preserving aggregate reaction deltas."""
        deltas = {key: value for key, value in reactions_delta.items() if value}
        if not deltas:
            return
        async with acquire_connection() as conn:
            await conn.executemany(
                """
                INSERT INTO screw.flud_message_reactions
                    (chat_id, message_id, reaction_type, reaction_count, updated_at)
                VALUES ($1, $2, $3, GREATEST($4, 0), NOW())
                ON CONFLICT (chat_id, message_id, reaction_type) DO UPDATE SET
                    reaction_count = GREATEST(screw.flud_message_reactions.reaction_count + $4, 0),
                    updated_at = NOW()
                """,
                [(chat_id, message_id, reaction_type, delta) for reaction_type, delta in deltas.items()],
            )

    @staticmethod
    def _period_end_exclusive(period_end: date) -> date:
        return period_end + timedelta(days=1)

    @staticmethod
    async def get_summary(period_start: date, period_end: date, chat_id: int) -> dict[str, Any]:
        """Return total messages, replies, users and reactions for a period."""
        async with acquire_connection() as conn:
            row = await conn.fetchrow(
                """
                WITH messages AS (
                    SELECT *
                    FROM screw.flud_messages
                    WHERE chat_id = $1
                      AND sent_at >= $2::date
                      AND sent_at < $3::date
                ),
                reactions AS (
                    SELECT COALESCE(SUM(r.reaction_count), 0) AS total_reactions
                    FROM screw.flud_message_reactions r
                    JOIN messages m ON m.chat_id = r.chat_id AND m.message_id = r.message_id
                )
                SELECT
                    COUNT(*)::int AS message_count,
                    COUNT(
                        DISTINCT COALESCE(
                            user_id::text,
                            telegram_user_id::text,
                            source_author_hash
                        )
                    )::int AS active_users_count,
                    COALESCE(SUM(CASE WHEN is_reply THEN 1 ELSE 0 END), 0)::int AS reply_count,
                    COALESCE(MAX(reactions.total_reactions), 0)::int AS reaction_count
                FROM messages, reactions
                """,
                chat_id,
                period_start,
                FludRepo._period_end_exclusive(period_end),
            )
            return dict(row) if row else {
                "message_count": 0,
                "active_users_count": 0,
                "reply_count": 0,
                "reaction_count": 0,
            }

    @staticmethod
    async def get_active_users_count(period_start: date, period_end: date, chat_id: int) -> int:
        summary = await FludRepo.get_summary(period_start, period_end, chat_id)
        return int(summary.get("active_users_count") or 0)

    @staticmethod
    async def get_message_type_counts(period_start: date, period_end: date, chat_id: int) -> dict[str, int]:
        async with acquire_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT message_type, COUNT(*)::int AS count
                FROM screw.flud_messages
                WHERE chat_id = $1
                  AND sent_at >= $2::date
                  AND sent_at < $3::date
                GROUP BY message_type
                """,
                chat_id,
                period_start,
                FludRepo._period_end_exclusive(period_end),
            )
            return {row["message_type"]: row["count"] for row in rows}

    @staticmethod
    async def get_hourly_activity(period_start: date, period_end: date, chat_id: int) -> list[dict[str, Any]]:
        async with acquire_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT EXTRACT(HOUR FROM sent_at AT TIME ZONE 'Europe/Moscow')::int AS hour,
                       COUNT(*)::int AS count
                FROM screw.flud_messages
                WHERE chat_id = $1
                  AND sent_at >= $2::date
                  AND sent_at < $3::date
                GROUP BY hour
                ORDER BY count DESC, hour
                """,
                chat_id,
                period_start,
                FludRepo._period_end_exclusive(period_end),
            )
            return [dict(row) for row in rows]

    @staticmethod
    async def get_daily_activity(period_start: date, period_end: date, chat_id: int) -> list[dict[str, Any]]:
        async with acquire_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT (sent_at AT TIME ZONE 'Europe/Moscow')::date AS day,
                       COUNT(*)::int AS count
                FROM screw.flud_messages
                WHERE chat_id = $1
                  AND sent_at >= $2::date
                  AND sent_at < $3::date
                GROUP BY day
                ORDER BY count DESC, day
                """,
                chat_id,
                period_start,
                FludRepo._period_end_exclusive(period_end),
            )
            return [dict(row) for row in rows]

    @staticmethod
    async def _top_messages(
        period_start: date,
        period_end: date,
        chat_id: int,
        limit: int,
        *,
        predicate_sql: str = "TRUE",
    ) -> list[dict[str, Any]]:
        async with acquire_connection() as conn:
            rows = await conn.fetch(
                f"""
                SELECT
                    m.chat_id,
                    m.message_id,
                    m.user_id,
                    m.telegram_user_id,
                    COALESCE(u.full_name, u.username, 'участник') AS author_name,
                    m.message_type,
                    m.text_preview,
                    COALESCE(SUM(r.reaction_count), 0)::int AS reaction_count,
                    (
                        SELECT r2.reaction_type
                        FROM screw.flud_message_reactions r2
                        WHERE r2.chat_id = m.chat_id
                          AND r2.message_id = m.message_id
                        ORDER BY r2.reaction_count DESC, r2.reaction_type
                        LIMIT 1
                    ) AS top_reaction_type
                FROM screw.flud_messages m
                LEFT JOIN screw.users u ON u.id = m.user_id
                LEFT JOIN screw.flud_message_reactions r
                    ON r.chat_id = m.chat_id AND r.message_id = m.message_id
                WHERE m.chat_id = $1
                  AND m.sent_at >= $2::date
                  AND m.sent_at < $3::date
                  AND {predicate_sql}
                GROUP BY m.chat_id, m.message_id, m.user_id, m.telegram_user_id,
                         u.full_name, u.username, m.message_type, m.text_preview, m.sent_at
                ORDER BY reaction_count DESC, m.sent_at DESC
                LIMIT $4
                """,
                chat_id,
                period_start,
                FludRepo._period_end_exclusive(period_end),
                limit,
            )
            return [dict(row) for row in rows]

    @staticmethod
    async def get_top_reacted_messages(
        period_start: date,
        period_end: date,
        chat_id: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        return await FludRepo._top_messages(
            period_start,
            period_end,
            chat_id,
            limit,
            predicate_sql="EXISTS (SELECT 1 FROM screw.flud_message_reactions rx "
            "WHERE rx.chat_id = m.chat_id AND rx.message_id = m.message_id AND rx.reaction_count > 0)",
        )

    @staticmethod
    async def get_top_media_messages(
        period_start: date,
        period_end: date,
        chat_id: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        return await FludRepo._top_messages(
            period_start,
            period_end,
            chat_id,
            limit,
            predicate_sql="(m.has_photo OR m.has_video OR m.has_animation OR m.has_sticker OR m.has_voice OR m.has_document)",
        )

    @staticmethod
    async def get_top_text_messages(
        period_start: date,
        period_end: date,
        chat_id: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        return await FludRepo._top_messages(
            period_start,
            period_end,
            chat_id,
            limit,
            predicate_sql="m.has_text",
        )

    @staticmethod
    async def create_digest_run(
        digest_type: str,
        period_start: date,
        period_end: date,
        chat_id: int,
        *,
        target_chat_id: int | None = None,
        created_by: int | None = None,
    ) -> int:
        async with acquire_connection() as conn:
            return await conn.fetchval(
                """
                INSERT INTO screw.flud_digest_runs
                    (digest_type, period_start, period_end, chat_id, target_chat_id, created_by)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                digest_type,
                period_start,
                period_end,
                chat_id,
                target_chat_id,
                created_by,
            )

    @staticmethod
    async def mark_digest_published(run_id: int, target_chat_id: int, message_id: int | None) -> None:
        async with acquire_connection() as conn:
            await conn.execute(
                """
                UPDATE screw.flud_digest_runs
                SET target_chat_id = $2,
                    message_id = $3,
                    status = 'published',
                    published_at = NOW(),
                    error_summary = NULL
                WHERE id = $1
                """,
                run_id,
                target_chat_id,
                message_id,
            )

    @staticmethod
    async def mark_digest_failed(run_id: int, error_summary: str) -> None:
        async with acquire_connection() as conn:
            await conn.execute(
                """
                UPDATE screw.flud_digest_runs
                SET status = 'failed',
                    error_summary = $2
                WHERE id = $1
                """,
                run_id,
                error_summary[:500],
            )

    @staticmethod
    async def cleanup_old_flud_data(retention_days: int) -> int:
        """Delete flud data older than retention window."""
        if retention_days <= 0:
            return 0
        async with acquire_connection() as conn:
            result_reactions = await conn.execute(
                """
                DELETE FROM screw.flud_message_reactions r
                WHERE EXISTS (
                    SELECT 1
                    FROM screw.flud_messages m
                    WHERE m.chat_id = r.chat_id
                      AND m.message_id = r.message_id
                      AND m.sent_at < NOW() - ($1::int * INTERVAL '1 day')
                )
                """,
                retention_days,
            )
            result_messages = await conn.execute(
                """
                DELETE FROM screw.flud_messages
                WHERE sent_at < NOW() - ($1::int * INTERVAL '1 day')
                """,
                retention_days,
            )
            return parse_rowcount(result_reactions) + parse_rowcount(result_messages)
