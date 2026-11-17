-- The source's health per minute of event time (ADR-0045, decision 5: does
-- it get hot enough to slow down, is its power supply adequate, when does its
-- disk fill). `throttled_seconds` and `under_voltage_seconds` count the
-- readings with the flag set: one reading a second.

select
    date_trunc('minute', collected_at at time zone 'UTC') at time zone 'UTC' as minute_start,
    source,
    count(*) as readings,
    max(cpu_temp_c) as cpu_temp_max_c,
    avg(cpu_temp_c) as cpu_temp_avg_c,
    avg(cpu_freq_mhz) as cpu_freq_avg_mhz,
    avg(load_1m) as load_1m_avg,
    count(*) filter (where throttled) as throttled_seconds,
    count(*) filter (where under_voltage) as under_voltage_seconds,
    max(disk_used_bytes) as disk_used_bytes,
    max(disk_total_bytes) as disk_total_bytes
from {{ ref('stg_events__health') }}
group by 1, 2
