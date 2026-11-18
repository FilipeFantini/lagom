-- The source's health per minute of event time (ADR-0045, decision 5: does
-- it get hot enough to slow down, is its power supply adequate, when does its
-- disk fill). What each column means is in
-- macros/streaming/health_minute_rows.sql. Read from streaming.health_minute,
-- with the minutes it may have wrong computed again from the live table when
-- the view is read (macros/streaming/changed_minutes.sql).

with minutes as (
    {{ current_minutes(
        ref('stg_events__health'), ref('health_minute'), health_minute_rows, '0 seconds',
        ['minute_start', 'source', 'readings', 'cpu_temp_max_c', 'cpu_temp_avg_c',
         'cpu_freq_avg_mhz', 'load_1m_avg', 'throttled_seconds', 'under_voltage_seconds',
         'disk_used_bytes', 'disk_total_bytes']
    ) }}
)

select
    minute_start,
    source,
    readings,
    cpu_temp_max_c,
    cpu_temp_avg_c,
    cpu_freq_avg_mhz,
    load_1m_avg,
    throttled_seconds,
    under_voltage_seconds,
    disk_used_bytes,
    disk_total_bytes
from minutes
