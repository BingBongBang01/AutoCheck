-- Log_Inspection: monthly range partitioning on inspection_date, JSONB GIN index.
-- Run once against PostgreSQL before the Django app starts using this table
-- (Django's migration for this app should be a no-op / RunSQL wrapper around this file).

CREATE TABLE IF NOT EXISTS log_inspection (
    inspection_id   UUID NOT NULL DEFAULT gen_random_uuid(),
    device_id       UUID NOT NULL REFERENCES core_device (device_id),
    inspection_date DATE NOT NULL,
    inspector_name  VARCHAR(255) NOT NULL,
    raw_metrics     JSONB NOT NULL DEFAULT '{}'::jsonb,
    health_status   VARCHAR(16) NOT NULL DEFAULT 'NORMAL',
    PRIMARY KEY (inspection_id, inspection_date)
) PARTITION BY RANGE (inspection_date);

CREATE INDEX IF NOT EXISTS idx_log_inspection_device ON log_inspection (device_id);
CREATE INDEX IF NOT EXISTS idx_log_inspection_status ON log_inspection (health_status);

-- Function + trigger-free approach: partitions are created ahead of time by a
-- scheduled job (see app_core/management/commands or a cron) calling this
-- helper for the next N months. Kept idempotent so it is safe to re-run.
CREATE OR REPLACE FUNCTION create_log_inspection_partition(target_month DATE)
RETURNS void AS $$
DECLARE
    partition_name TEXT := 'log_inspection_' || to_char(target_month, 'YYYY_MM');
    range_start DATE := date_trunc('month', target_month);
    range_end DATE := range_start + INTERVAL '1 month';
BEGIN
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF log_inspection FOR VALUES FROM (%L) TO (%L);',
        partition_name, range_start, range_end
    );
    -- GIN index for deep JSONB search, created per-partition (not inherited automatically).
    EXECUTE format(
        'CREATE INDEX IF NOT EXISTS %I ON %I USING GIN (raw_metrics);',
        partition_name || '_raw_metrics_gin', partition_name
    );
END;
$$ LANGUAGE plpgsql;

-- Bootstrap: current month +/- 2 months. Extend via a monthly cron calling
-- SELECT create_log_inspection_partition(date_trunc('month', now() + interval '1 month'));
SELECT create_log_inspection_partition(date_trunc('month', now() - interval '2 month'));
SELECT create_log_inspection_partition(date_trunc('month', now() - interval '1 month'));
SELECT create_log_inspection_partition(date_trunc('month', now()));
SELECT create_log_inspection_partition(date_trunc('month', now() + interval '1 month'));
SELECT create_log_inspection_partition(date_trunc('month', now() + interval '2 month'));
