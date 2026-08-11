-- Схема БД SCREW v1 (этап 4)
-- Создаёт схему screw и все таблицы для attendance_bot

-- =============================================================================
-- Создание схемы и настройка search_path
-- =============================================================================
CREATE SCHEMA IF NOT EXISTS screw;
SET search_path TO screw;

-- =============================================================================
-- ENUM типы (PostgreSQL)
-- =============================================================================

-- Отделы организации
CREATE TYPE department_enum AS ENUM ('OZKO', 'OSB');

-- Статус пользователя
CREATE TYPE user_status_enum AS ENUM ('active', 'terminated');

-- Режим работы сотрудника
CREATE TYPE work_mode_enum AS ENUM ('HYBRID', 'HYBRID_PLUS', 'FULL_REMOTE', 'DUTY');

-- Выбор явки в опросе
CREATE TYPE attendance_choice_enum AS ENUM ('EVOLUTION', 'CSKA', 'REMOTE');

-- Вид статуса дня (отпуск/больничный/отгул/выходной)
CREATE TYPE day_status_kind_enum AS ENUM ('VACATION', 'SICK', 'LEAVE', 'DAY_OFF');

-- Тип события/нарушения
CREATE TYPE event_type_enum AS ENUM (
    'NO_CHECKIN', 'CONFLICT_STATUS'
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'event_type_enum'
          AND n.nspname = 'screw'
          AND e.enumlabel = 'REPORT_DAILY_SENT'
    ) THEN
        ALTER TYPE screw.event_type_enum ADD VALUE 'REPORT_DAILY_SENT';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'event_type_enum'
          AND n.nspname = 'screw'
          AND e.enumlabel = 'REPORT_DAILY_FAILED'
    ) THEN
        ALTER TYPE screw.event_type_enum ADD VALUE 'REPORT_DAILY_FAILED';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'event_type_enum'
          AND n.nspname = 'screw'
          AND e.enumlabel = 'REPORT_MONTHLY_SENT'
    ) THEN
        ALTER TYPE screw.event_type_enum ADD VALUE 'REPORT_MONTHLY_SENT';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'event_type_enum'
          AND n.nspname = 'screw'
          AND e.enumlabel = 'REPORT_MONTHLY_FAILED'
    ) THEN
        ALTER TYPE screw.event_type_enum ADD VALUE 'REPORT_MONTHLY_FAILED';
    END IF;

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

    IF NOT EXISTS (
        SELECT 1
        FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'event_type_enum'
          AND n.nspname = 'screw'
          AND e.enumlabel = 'OZKO_POST_APPROVE_NOTICE'
    ) THEN
        ALTER TYPE screw.event_type_enum ADD VALUE 'OZKO_POST_APPROVE_NOTICE';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'event_type_enum'
          AND n.nspname = 'screw'
          AND e.enumlabel = 'BIRTHDAY_NOTIFY_SENT'
    ) THEN
        ALTER TYPE screw.event_type_enum ADD VALUE 'BIRTHDAY_NOTIFY_SENT';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'event_type_enum'
          AND n.nspname = 'screw'
          AND e.enumlabel = 'ADMIN_ACTION'
    ) THEN
        ALTER TYPE screw.event_type_enum ADD VALUE 'ADMIN_ACTION';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'event_type_enum'
          AND n.nspname = 'screw'
          AND e.enumlabel = 'MANAGER_ACTION'
    ) THEN
        ALTER TYPE screw.event_type_enum ADD VALUE 'MANAGER_ACTION';
    END IF;
END $$;

-- Канал уведомления
CREATE TYPE notify_channel_enum AS ENUM ('violations_chat', 'user_dm', 'none');

-- Статус опроса
CREATE TYPE poll_status_enum AS ENUM ('open', 'closed', 'deleted');

-- =============================================================================
-- Справочник отделов
-- =============================================================================
CREATE TABLE IF NOT EXISTS org_departments (
    id SERIAL PRIMARY KEY,
    code department_enum NOT NULL UNIQUE,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE org_departments IS 'Справочник отделов организации (ОЗКО, ОСБ)';

-- Заполняем справочник отделов
INSERT INTO org_departments (code, name, description) VALUES
    ('OZKO', 'ОЗКО', 'Отдел защиты конечного оборудования'),
    ('OSB', 'ОСБ', 'Отдел сетевой безопасности')
ON CONFLICT (code) DO NOTHING;

-- =============================================================================
-- Пользователи
-- =============================================================================
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL UNIQUE,
    username VARCHAR(255),
    first_name VARCHAR(255),
    last_name VARCHAR(255),
    full_name VARCHAR(500),
    role VARCHAR(50) NOT NULL DEFAULT 'employee',
    department_id INT REFERENCES org_departments(id),
    work_mode work_mode_enum NOT NULL DEFAULT 'HYBRID',
    status user_status_enum NOT NULL DEFAULT 'active',
    birthday DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id);
CREATE INDEX IF NOT EXISTS idx_users_department_id ON users(department_id);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_users_birthday_md
    ON users (EXTRACT(MONTH FROM birthday), EXTRACT(DAY FROM birthday))
    WHERE birthday IS NOT NULL AND status = 'active';

COMMENT ON TABLE users IS 'Пользователи системы (сотрудники, менеджеры, админы)';

-- =============================================================================
-- Приглашения на регистрацию (deep-link токены)
-- =============================================================================
CREATE TABLE IF NOT EXISTS registration_invites (
    id BIGSERIAL PRIMARY KEY,
    token VARCHAR(64) NOT NULL UNIQUE,
    department_id INT NOT NULL REFERENCES org_departments(id),
    expected_full_name VARCHAR(500),
    work_mode work_mode_enum NOT NULL DEFAULT 'HYBRID',
    created_by BIGINT REFERENCES users(id),
    used_by BIGINT REFERENCES users(id),
    used_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_registration_invites_token ON registration_invites(token);

COMMENT ON TABLE registration_invites IS 'Токены-приглашения для регистрации сотрудников';

-- =============================================================================
-- Заявки на регистрацию (ожидают подтверждения менеджером)
-- =============================================================================
CREATE TABLE IF NOT EXISTS registration_requests (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT NOT NULL,
    invite_id BIGINT NOT NULL REFERENCES registration_invites(id),
    username VARCHAR(255),
    first_name VARCHAR(255),
    last_name VARCHAR(255),
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    reviewed_by BIGINT REFERENCES users(id),
    reviewed_at TIMESTAMPTZ,
    reject_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_registration_requests_telegram_id ON registration_requests(telegram_id);
CREATE INDEX IF NOT EXISTS idx_registration_requests_status ON registration_requests(status);

COMMENT ON TABLE registration_requests IS 'Заявки на регистрацию, ожидающие подтверждения';

-- =============================================================================
-- Запуски опросов (один опрос в день)
-- =============================================================================
CREATE TABLE IF NOT EXISTS poll_runs (
    id BIGSERIAL PRIMARY KEY,
    poll_date DATE NOT NULL,
    chat_id BIGINT NOT NULL,
    message_id BIGINT,
    telegram_poll_id TEXT,
    status poll_status_enum NOT NULL DEFAULT 'open',
    published_at TIMESTAMPTZ,
    closes_at TIMESTAMPTZ,
    reminder_sent_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    expected_users_snapshot_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_poll_runs_date ON poll_runs(poll_date);
CREATE INDEX IF NOT EXISTS idx_poll_runs_status ON poll_runs(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_poll_runs_telegram_poll_id ON poll_runs(telegram_poll_id) WHERE telegram_poll_id IS NOT NULL;

-- Миграции: добавить колонки если таблица уже существует
ALTER TABLE poll_runs ADD COLUMN IF NOT EXISTS telegram_poll_id TEXT;
ALTER TABLE poll_runs ADD COLUMN IF NOT EXISTS closes_at TIMESTAMPTZ;
ALTER TABLE poll_runs ADD COLUMN IF NOT EXISTS expected_users_snapshot_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS poll_expected_users (
    poll_run_id BIGINT NOT NULL REFERENCES poll_runs(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    department_id INT NOT NULL REFERENCES org_departments(id),
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (poll_run_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_poll_expected_users_department_poll
    ON poll_expected_users (department_id, poll_run_id);
ALTER TABLE poll_runs ADD COLUMN IF NOT EXISTS reminder_message_id BIGINT;

-- Индекс для быстрого поиска просроченных открытых опросов
CREATE INDEX IF NOT EXISTS idx_poll_runs_status_closes_at ON poll_runs(status, closes_at) WHERE status = 'open';

COMMENT ON TABLE poll_runs IS 'Запуски ежедневных опросов явки';

-- =============================================================================
-- Отметки явки (факт)
-- =============================================================================
CREATE TABLE IF NOT EXISTS attendance_marks (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    poll_run_id BIGINT REFERENCES poll_runs(id),
    mark_date DATE NOT NULL,
    choice attendance_choice_enum NOT NULL,
    source VARCHAR(50) NOT NULL DEFAULT 'poll',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_attendance_marks_user_date ON attendance_marks(user_id, mark_date);
CREATE INDEX IF NOT EXISTS idx_attendance_marks_date ON attendance_marks(mark_date);

COMMENT ON TABLE attendance_marks IS 'Фактические отметки явки сотрудников';

-- =============================================================================
-- Статусы дней пользователей (отпуск/больничный/отгул)
-- =============================================================================
CREATE TABLE IF NOT EXISTS user_day_statuses (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    status_date DATE NOT NULL,
    kind day_status_kind_enum NOT NULL,
    comment TEXT,
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_user_day_statuses_user_date ON user_day_statuses(user_id, status_date);
CREATE INDEX IF NOT EXISTS idx_user_day_statuses_date ON user_day_statuses(status_date);
CREATE INDEX IF NOT EXISTS idx_user_day_statuses_kind ON user_day_statuses(kind);

COMMENT ON TABLE user_day_statuses IS 'Статусы дней пользователей (отпуск, больничный, отгул)';

-- =============================================================================
-- События и нарушения (с дедупликацией и retry доставки)
-- =============================================================================
CREATE TABLE IF NOT EXISTS events (
    id BIGSERIAL PRIMARY KEY,
    event_type event_type_enum NOT NULL,
    dedup_key VARCHAR(255) NOT NULL UNIQUE,
    user_id BIGINT REFERENCES users(id),
    event_date DATE,
    payload JSONB,
    notify_channel notify_channel_enum NOT NULL DEFAULT 'violations_chat',
    is_notified BOOLEAN NOT NULL DEFAULT FALSE,
    notify_attempts INT NOT NULL DEFAULT 0,
    notified_at TIMESTAMPTZ,
    notify_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_dedup_key ON events(dedup_key);
CREATE INDEX IF NOT EXISTS idx_events_type_created ON events(event_type, created_at);
CREATE INDEX IF NOT EXISTS idx_events_user_date ON events(user_id, event_date);
CREATE INDEX IF NOT EXISTS idx_events_not_notified ON events(is_notified, notify_attempts)
    WHERE is_notified = FALSE;

COMMENT ON TABLE events IS 'События и нарушения с дедупликацией и retry-логикой уведомлений';

-- =============================================================================
-- Feature flags
-- =============================================================================
CREATE TABLE IF NOT EXISTS feature_flags (
    id SERIAL PRIMARY KEY,
    flag_name VARCHAR(100) NOT NULL UNIQUE,
    is_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_feature_flags_name ON feature_flags(flag_name);

COMMENT ON TABLE feature_flags IS 'Флаги функций для постепенного включения фич';

-- =============================================================================
-- Миграции ENUM типов (идемпотентные)
-- =============================================================================

-- Добавить DAY_OFF в day_status_kind_enum (если ещё нет)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_enum
        WHERE enumtypid = 'screw.day_status_kind_enum'::regtype
        AND enumlabel = 'DAY_OFF'
    ) THEN
        ALTER TYPE screw.day_status_kind_enum ADD VALUE 'DAY_OFF';
    END IF;
END $$;

-- =============================================================================
-- Комментарии к схеме
-- =============================================================================
COMMENT ON SCHEMA screw IS 'Основная схема проекта SCREW — учёт явки';

-- =============================================================================
-- Poll configuration (singleton row — настройки опросов из админ-панели)
-- =============================================================================
CREATE TABLE IF NOT EXISTS screw.poll_config (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    poll_duration_seconds INTEGER NOT NULL DEFAULT 900,
    reminder_before_end_minutes INTEGER,
    poll_options JSONB NOT NULL DEFAULT '[
        {"key": "EVOLUTION", "label": "МФК «Эволюция»"},
        {"key": "CSKA", "label": "ЦСКА"},
        {"key": "REMOTE", "label": "Удалёнка"}
    ]'::jsonb,
    auto_send_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    auto_send_time TIME,
    office_requirement_hybrid INTEGER NOT NULL DEFAULT 8,
    office_requirement_hybrid_plus INTEGER NOT NULL DEFAULT 4,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    updated_by BIGINT
);

INSERT INTO screw.poll_config (id) VALUES (1) ON CONFLICT DO NOTHING;

COMMENT ON TABLE screw.poll_config IS 'Singleton-конфиг опросов: варианты, длительность, авто-отправка';
COMMENT ON COLUMN screw.poll_config.office_requirement_hybrid IS 'Норма офисных дней в месяц для режима HYBRID (по умолчанию 8)';
COMMENT ON COLUMN screw.poll_config.office_requirement_hybrid_plus IS 'Норма офисных дней в месяц для режима HYBRID_PLUS (по умолчанию 4)';

-- =============================================================================
-- Poll skip dates (праздники / пропуск опросов)
-- =============================================================================
CREATE TABLE IF NOT EXISTS screw.poll_skip_dates (
    id BIGSERIAL PRIMARY KEY,
    skip_date DATE NOT NULL UNIQUE,
    reason TEXT,
    created_by BIGINT REFERENCES screw.users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_poll_skip_dates_date
    ON screw.poll_skip_dates (skip_date);

COMMENT ON TABLE screw.poll_skip_dates IS 'Даты пропуска опросов (праздники, корпоративы и т.д.)';
