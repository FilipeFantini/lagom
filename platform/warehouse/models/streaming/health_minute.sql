-- The source's health per minute of event time: what source_health_minute,
-- and the health stream of pipeline_minute, read (ADR-0047, decision 8, its
-- second remedy). The columns are macros/streaming/health_minute_rows.sql's;
-- how the table is kept, as for ping_minute, is
-- macros/streaming/changed_minutes.sql.
{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key=['source', 'minute_start'],
    on_schema_change='fail',
    unlogged=true,
    pre_hook="{{ delete_trimmed_minutes(ref('stg_events__health')) }}",
) }}

{% if is_incremental() %}
    {{ changed_minutes(ref('stg_events__health'), this, health_minute_rows, '0 seconds') }}
{% else %}
    {{ health_minute_rows(ref('stg_events__health')) }}
{% endif %}
