-- Expected attendance poll participants and monthly stats delivery marker.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'event_type_enum'
          AND n.nspname = 'screw'
          AND e.enumlabel = 'MONTHLY_STATS_SENT'
    ) THEN
        ALTER TYPE screw.event_type_enum ADD VALUE 'MONTHLY_STATS_SENT';
    END IF;
END $$;

ALTER TABLE screw.poll_runs
    ADD COLUMN IF NOT EXISTS expected_users_snapshot_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS screw.poll_expected_users (
    poll_run_id BIGINT NOT NULL REFERENCES screw.poll_runs(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES screw.users(id) ON DELETE CASCADE,
    department_id INT NOT NULL REFERENCES screw.org_departments(id),
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (poll_run_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_poll_expected_users_department_poll
    ON screw.poll_expected_users (department_id, poll_run_id);
