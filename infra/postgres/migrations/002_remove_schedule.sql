-- Миграция: удаление таблиц графика (schedule_bot удалён)
-- Дата: 2026-03-25
--
-- ВАЖНО: Старые записи events с удалёнными EventType значениями
-- остаются в БД — PostgreSQL enum values нельзя удалить без пересоздания типа.

BEGIN;

-- Удаление schedule-таблиц
DROP TABLE IF EXISTS screw.schedule_overrides_audit CASCADE;
DROP TABLE IF EXISTS screw.schedule_expected_days CASCADE;
DROP TABLE IF EXISTS screw.schedule_imports CASCADE;

-- Удаление schedule-enum типов
DROP TYPE IF EXISTS screw.expected_status CASCADE;
DROP TYPE IF EXISTS screw.import_status CASCADE;

COMMIT;
