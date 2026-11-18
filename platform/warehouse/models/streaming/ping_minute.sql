-- The link per minute of event time, per source and target: what the views
-- of metrics/ about the link read (ADR-0047, decision 8, its second remedy).
-- The columns are macros/streaming/ping_minute_rows.sql's; how the table is
-- kept is macros/streaming/changed_minutes.sql.
--
-- An incremental table, built by the hourly `streaming` job: each build
-- computes again the minutes of the events stored since the last one (an
-- hour before it, for the writer's lag) and of the oldest hour, which the
-- archive trims, and replaces those rows; a first build, or
-- `--full-refresh`, computes every minute of the live table. UNLOGGED like
-- live.events: it is derived from it, a crash empties both, and it adds
-- nothing to the backup. It keeps what the live table holds, no more: a
-- minute whose events were all archived is deleted.
{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key=['source', 'ping_target', 'minute_start'],
    on_schema_change='fail',
    unlogged=true,
    pre_hook="{{ delete_trimmed_minutes(ref('stg_events__pings')) }}",
) }}

{% if is_incremental() %}
    {{ changed_minutes(ref('stg_events__pings'), this, ping_minute_rows, '5 seconds') }}
{% else %}
    {{ ping_minute_rows(ref('stg_events__pings')) }}
{% endif %}
