-- Every row of each new view against the original SQL over the same live table,
-- both ways; averages to 9 decimals. One statement per view, so one snapshot.
\pset footer off
select 'link_minute' as view, count(*) as differing_rows from (
  (select minute_start, source, ping_target, pings, lost_pings, rtt_p50_ms, rtt_p95_ms, round(jitter_ms::numeric, 9) from metrics.link_minute
   except all select minute_start, source, ping_target, pings, lost_pings, rtt_p50_ms, rtt_p95_ms, round(jitter_ms::numeric, 9) from orig.link_minute)
  union all
  (select minute_start, source, ping_target, pings, lost_pings, rtt_p50_ms, rtt_p95_ms, round(jitter_ms::numeric, 9) from orig.link_minute
   except all select minute_start, source, ping_target, pings, lost_pings, rtt_p50_ms, rtt_p95_ms, round(jitter_ms::numeric, 9) from metrics.link_minute)) d;
select 'link_outages' as view, count(*) as differing_rows from (
  (select * from metrics.link_outages except all select * from orig.link_outages)
  union all (select * from orig.link_outages except all select * from metrics.link_outages)) d;
select 'link_hour_profile' as view, count(*) as differing_rows from (
  (select * from metrics.link_hour_profile except all select * from orig.link_hour_profile)
  union all (select * from orig.link_hour_profile except all select * from metrics.link_hour_profile)) d;
select 'source_health_minute' as view, count(*) as differing_rows from (
  (select minute_start, source, readings, cpu_temp_max_c, round(cpu_temp_avg_c::numeric, 9), round(cpu_freq_avg_mhz::numeric, 9), round(load_1m_avg::numeric, 9), throttled_seconds, under_voltage_seconds, disk_used_bytes, disk_total_bytes from metrics.source_health_minute
   except all select minute_start, source, readings, cpu_temp_max_c, round(cpu_temp_avg_c::numeric, 9), round(cpu_freq_avg_mhz::numeric, 9), round(load_1m_avg::numeric, 9), throttled_seconds, under_voltage_seconds, disk_used_bytes, disk_total_bytes from orig.source_health_minute)
  union all
  (select minute_start, source, readings, cpu_temp_max_c, round(cpu_temp_avg_c::numeric, 9), round(cpu_freq_avg_mhz::numeric, 9), round(load_1m_avg::numeric, 9), throttled_seconds, under_voltage_seconds, disk_used_bytes, disk_total_bytes from orig.source_health_minute
   except all select minute_start, source, readings, cpu_temp_max_c, round(cpu_temp_avg_c::numeric, 9), round(cpu_freq_avg_mhz::numeric, 9), round(load_1m_avg::numeric, 9), throttled_seconds, under_voltage_seconds, disk_used_bytes, disk_total_bytes from metrics.source_health_minute)) d;
select 'pipeline_minute' as view, count(*) as differing_rows from (
  (select * from metrics.pipeline_minute except all select * from orig.pipeline_minute)
  union all (select * from orig.pipeline_minute except all select * from metrics.pipeline_minute)) d;
select (select count(*) from metrics.link_minute) as link_minute_rows, (select count(*) from metrics.link_outages) as outages,
       (select count(*) from metrics.link_hour_profile) as hour_cells, (select count(*) from metrics.source_health_minute) as health_minutes,
       (select count(*) from metrics.pipeline_minute) as pipeline_rows, (select count(*) from live.events) as events;
