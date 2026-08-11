"""Read model for monthly attendance statistics."""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from typing import Any

import asyncpg

from core.screw_core.db.tx import acquire_connection


class MonthlyStatsRepo:
    """Load poll, eligibility and response facts for one calendar month."""

    @staticmethod
    async def load_period(year: int, month: int, department_id: int) -> dict[str, Any]:
        start = date(year, month, 1)
        end = date(year, month, monthrange(year, month)[1])

        async with acquire_connection() as conn:
            try:
                poll_rows = await conn.fetch(
                    """
                    SELECT id, poll_date, published_at, expected_users_snapshot_at
                    FROM screw.poll_runs
                    WHERE poll_date BETWEEN $1 AND $2
                    ORDER BY poll_date, id
                    """,
                    start,
                    end,
                )
            except asyncpg.UndefinedColumnError:
                poll_rows = await conn.fetch(
                    """
                    SELECT id, poll_date, published_at, NULL::timestamptz AS expected_users_snapshot_at
                    FROM screw.poll_runs
                    WHERE poll_date BETWEEN $1 AND $2
                    ORDER BY poll_date, id
                    """,
                    start,
                    end,
                )

            polls = [dict(row) for row in poll_rows]
            if not polls:
                return {
                    "polls": [],
                    "expected": [],
                    "responses": [],
                    "approximate_poll_ids": [],
                }

            poll_ids = [int(row["id"]) for row in polls]
            snapshot_poll_ids = [
                int(row["id"])
                for row in polls
                if row.get("expected_users_snapshot_at") is not None
            ]
            fallback_poll_ids = sorted(set(poll_ids) - set(snapshot_poll_ids))

            expected: list[dict[str, Any]] = []
            if snapshot_poll_ids:
                snapshot_rows = await conn.fetch(
                    """
                    SELECT pe.poll_run_id, pe.user_id, pe.department_id, u.full_name
                    FROM screw.poll_expected_users pe
                    JOIN screw.users u ON u.id = pe.user_id
                    WHERE pe.poll_run_id = ANY($1::bigint[])
                      AND pe.department_id = $2
                    ORDER BY pe.poll_run_id, u.full_name
                    """,
                    snapshot_poll_ids,
                    department_id,
                )
                expected.extend(dict(row) for row in snapshot_rows)

            if fallback_poll_ids:
                fallback_rows = await conn.fetch(
                    """
                    SELECT p.id AS poll_run_id, u.id AS user_id, u.department_id, u.full_name
                    FROM screw.poll_runs p
                    JOIN screw.users u ON u.department_id = $2
                    WHERE p.id = ANY($1::bigint[])
                      AND u.created_at::date <= p.poll_date
                      AND u.role <> 'no_tg_worker'
                      AND (u.work_mode IS NULL OR u.work_mode <> 'DUTY')
                      AND (
                          u.status = 'active'
                          OR (u.status = 'terminated' AND u.updated_at::date > p.poll_date)
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM screw.user_day_statuses uds
                          WHERE uds.user_id = u.id
                            AND uds.status_date = p.poll_date
                            AND uds.kind IN ('VACATION', 'SICK', 'LEAVE')
                      )
                    ORDER BY p.id, u.full_name
                    """,
                    fallback_poll_ids,
                    department_id,
                )
                expected.extend(dict(row) for row in fallback_rows)

            response_rows = await conn.fetch(
                """
                SELECT am.poll_run_id,
                       am.user_id,
                       u.full_name,
                       pr.poll_date,
                       am.created_at AS responded_at,
                       EXTRACT(EPOCH FROM (am.created_at - pr.published_at)) AS response_seconds
                FROM screw.attendance_marks am
                JOIN screw.poll_runs pr ON pr.id = am.poll_run_id
                JOIN screw.users u ON u.id = am.user_id
                WHERE am.poll_run_id = ANY($1::bigint[])
                  AND pr.published_at IS NOT NULL
                  AND am.created_at IS NOT NULL
                ORDER BY am.poll_run_id, response_seconds, am.id
                """,
                poll_ids,
            )

            return {
                "polls": polls,
                "expected": expected,
                "responses": [dict(row) for row in response_rows],
                "approximate_poll_ids": fallback_poll_ids,
            }
