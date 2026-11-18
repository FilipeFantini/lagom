{#
  The source's health per minute of event time (UTC), from a relation of
  readings with the columns of stg_events__health (ADR-0045, decision 5: does
  it get hot enough to slow down, is its power supply adequate, when does its
  disk fill). Its rows are `streaming.health_minute`, which
  `source_health_minute` and the health stream of `pipeline_minute` are read
  from (changed_minutes.sql says how they are kept).

  `throttled_seconds` and `under_voltage_seconds` count the readings with the
  flag set: one reading a second. Lateness is as in ping_minute_rows.sql.
  No reading depends on the one before it, so a stretch of time needs no
  events before it.
#}
{% macro health_minute_rows(health) %}
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
        max(disk_total_bytes) as disk_total_bytes,
        count(*) filter (where received_at - collected_at > interval '1 minute') as late_events,
        percentile_cont(0.5) within group (
            order by extract(epoch from received_at - collected_at)
        ) as lateness_p50_seconds,
        max(extract(epoch from received_at - collected_at))::double precision as lateness_max_seconds,
        max(received_at) as max_received_at
    from {{ health }}
    group by minute_start, source
{% endmacro %}
