-- The tables of minutes against a snapshot taken before (snap.*), both ways.
select 'ping_minute' as t, count(*) from (
  (select minute_start, source, ping_target, pings, lost_pings, rtt_p50_ms, rtt_p95_ms, round(jitter_ms::numeric, 9), rtts, run_started_at, run_last_lost_at, run_lost_pings, run_starts_outage, late_events, lateness_p50_seconds, lateness_max_seconds, max_received_at from streaming.ping_minute
   except all select minute_start, source, ping_target, pings, lost_pings, rtt_p50_ms, rtt_p95_ms, round(jitter_ms::numeric, 9), rtts, run_started_at, run_last_lost_at, run_lost_pings, run_starts_outage, late_events, lateness_p50_seconds, lateness_max_seconds, max_received_at from snap.ping_minute)
  union all
  (select minute_start, source, ping_target, pings, lost_pings, rtt_p50_ms, rtt_p95_ms, round(jitter_ms::numeric, 9), rtts, run_started_at, run_last_lost_at, run_lost_pings, run_starts_outage, late_events, lateness_p50_seconds, lateness_max_seconds, max_received_at from snap.ping_minute
   except all select minute_start, source, ping_target, pings, lost_pings, rtt_p50_ms, rtt_p95_ms, round(jitter_ms::numeric, 9), rtts, run_started_at, run_last_lost_at, run_lost_pings, run_starts_outage, late_events, lateness_p50_seconds, lateness_max_seconds, max_received_at from streaming.ping_minute)) d
union all
select 'health_minute', count(*) from (
  (select minute_start, source, readings, round(cpu_temp_avg_c::numeric, 9), round(load_1m_avg::numeric, 9), round(cpu_freq_avg_mhz::numeric, 9), cpu_temp_max_c, throttled_seconds, under_voltage_seconds, disk_used_bytes, disk_total_bytes, late_events, lateness_p50_seconds, lateness_max_seconds, max_received_at from streaming.health_minute
   except all select minute_start, source, readings, round(cpu_temp_avg_c::numeric, 9), round(load_1m_avg::numeric, 9), round(cpu_freq_avg_mhz::numeric, 9), cpu_temp_max_c, throttled_seconds, under_voltage_seconds, disk_used_bytes, disk_total_bytes, late_events, lateness_p50_seconds, lateness_max_seconds, max_received_at from snap.health_minute)
  union all
  (select minute_start, source, readings, round(cpu_temp_avg_c::numeric, 9), round(load_1m_avg::numeric, 9), round(cpu_freq_avg_mhz::numeric, 9), cpu_temp_max_c, throttled_seconds, under_voltage_seconds, disk_used_bytes, disk_total_bytes, late_events, lateness_p50_seconds, lateness_max_seconds, max_received_at from snap.health_minute
   except all select minute_start, source, readings, round(cpu_temp_avg_c::numeric, 9), round(load_1m_avg::numeric, 9), round(cpu_freq_avg_mhz::numeric, 9), cpu_temp_max_c, throttled_seconds, under_voltage_seconds, disk_used_bytes, disk_total_bytes, late_events, lateness_p50_seconds, lateness_max_seconds, max_received_at from streaming.health_minute)) d;
