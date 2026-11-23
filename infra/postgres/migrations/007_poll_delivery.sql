BEGIN;

ALTER TABLE screw.poll_runs ADD COLUMN IF NOT EXISTS option_snapshot JSONB;
ALTER TABLE screw.poll_runs ADD COLUMN IF NOT EXISTS summary_pending BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE screw.poll_runs ADD COLUMN IF NOT EXISTS summary_ignore_day_off BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS screw.poll_vote_updates (
    poll_run_id BIGINT NOT NULL REFERENCES screw.poll_runs(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES screw.users(id) ON DELETE CASCADE,
    update_id BIGINT NOT NULL,
    PRIMARY KEY (poll_run_id, user_id)
);

CREATE TABLE IF NOT EXISTS screw.telegram_report_deliveries (
    delivery_key TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('sending', 'sent', 'failed', 'uncertain')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMIT;
