drop schema if exists orig cascade;
create schema orig;
create view orig.stg_events__pings as
-- One row per ping result, typed from the event's payload (ADR-0045,
-- decision 4). A lost ping is an event too, with no round trip: its rtt_ms is
-- null here whatever the payload says, so a producer that writes 0 or -1 for
-- a lost reply cannot pull the percentiles down. Read only by the views in
-- metrics/.

select
    event_id,
    source,
    payload ->> 'target' as ping_target,
    collected_at,
    received_at,
    (payload ->> 'lost')::boolean as lost,
    case
        when not (payload ->> 'lost')::boolean then (payload ->> 'rtt_ms')::double precision
    end as rtt_ms
from live.events
where signal = 'ping'
;
create view orig.stg_events__health as
-- One row per health reading of the source, typed from the event's payload
-- (ADR-0045, decision 4). Read only by the views in metrics/.

select
    event_id,
    source,
    collected_at,
    received_at,
    (payload ->> 'cpu_temp_c')::double precision as cpu_temp_c,
    (payload ->> 'cpu_freq_mhz')::double precision as cpu_freq_mhz,
    (payload ->> 'throttled')::boolean as throttled,
    (payload ->> 'under_voltage')::boolean as under_voltage,
    (payload ->> 'load_1m')::double precision as load_1m,
    (payload ->> 'mem_used_bytes')::bigint as mem_used_bytes,
    (payload ->> 'mem_total_bytes')::bigint as mem_total_bytes,
    (payload ->> 'disk_used_bytes')::bigint as disk_used_bytes,
    (payload ->> 'disk_total_bytes')::bigint as disk_total_bytes
from live.events
where signal = 'health'
;
create view orig.link_minute as
-- The link, per minute of event time, per source and target (ADR-0045,
-- decision 5: "is it good enough for a video call", and the loss to the
-- router against the loss beyond it, by target).
--
-- The minute is cut from collected_at, the source's clock, in UTC: an event
-- delivered hours late lands in its own minute, and the next query counts
-- it. No window ever closes. `pings` says whether a minute is complete (60
-- a target).
--
-- Jitter is the mean absolute difference between consecutive answered round
-- trips to the same target (RFC 3550's idea, without its smoothing); a pair
-- more than `gap` seconds apart (lost pings or no events between them) is not
-- consecutive and is left out.
-- Percentiles are exact (percentile_cont) over the minute's answered pings; a
-- minute with none has null latencies.



with pings as (
    select
        source,
        ping_target,
        collected_at,
        lost,
        rtt_ms,
        date_trunc('minute', collected_at at time zone 'UTC') at time zone 'UTC' as minute_start
    from orig.stg_events__pings
),

answered as (
    select
        minute_start,
        source,
        ping_target,
        collected_at,
        rtt_ms,
        lag(rtt_ms) over by_target as previous_rtt_ms,
        lag(collected_at) over by_target as previous_at
    from pings
    where not lost
    window by_target as (partition by source, ping_target order by collected_at)
),

steps as (
    select
        minute_start,
        source,
        ping_target,
        abs(rtt_ms - previous_rtt_ms) as rtt_step_ms
    from answered
    where collected_at - previous_at <= interval '5 seconds'
),

jitter as (
    select
        minute_start,
        source,
        ping_target,
        avg(rtt_step_ms) as jitter_ms
    from steps
    group by minute_start, source, ping_target
),

per_minute as (
    select
        minute_start,
        source,
        ping_target,
        count(*) as pings,
        count(*) filter (where lost) as lost_pings,
        percentile_cont(0.5) within group (order by rtt_ms) as rtt_p50_ms,
        percentile_cont(0.95) within group (order by rtt_ms) as rtt_p95_ms
    from pings
    group by minute_start, source, ping_target
)

select
    m.minute_start,
    m.source,
    m.ping_target,
    m.pings,
    m.lost_pings,
    m.rtt_p50_ms,
    m.rtt_p95_ms,
    j.jitter_ms
from per_minute as m
left join jitter as j
    on m.minute_start = j.minute_start and m.source = j.source and m.ping_target = j.ping_target
;
create view orig.link_outages as
-- Outages of the link per source and target (ADR-0045, decision 5: "how long
-- was it down"): a run of consecutive lost pings to one target, in event time.
--
-- A ping starts an outage when it is lost and the ping before it to the same
-- target was not lost, or was more than `gap` seconds earlier: with no events
-- in between (the source down) nothing says the link stayed down, so two
-- runs either side of the gap stay two. The running count of starts numbers
-- the outages. A ping that arrives late is placed by its collected_at, so the
-- next query sees the runs as they happened. A single lost ping is an outage
-- of one second: the reader filters by duration.
--
-- The duration is from the first lost ping to the last, plus the one-second
-- sampling interval.



with pings as (
    select
        source,
        ping_target,
        collected_at,
        lost,
        case
            when
                lost
                and (
                    not coalesce(lag(lost) over by_target, false)
                    or collected_at - lag(collected_at) over by_target
                    > interval '5 seconds'
                )
                then 1
            else 0
        end as starts_outage
    from orig.stg_events__pings
    window by_target as (partition by source, ping_target order by collected_at)
),

numbered as (
    select
        source,
        ping_target,
        collected_at,
        lost,
        sum(starts_outage) over (
            partition by source, ping_target order by collected_at
            rows between unbounded preceding and current row
        ) as outage_number
    from pings
)

select
    source || '/' || ping_target || '/' || to_char(
        min(collected_at) at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
    ) as outage_id,
    source,
    ping_target,
    min(collected_at) as started_at,
    max(collected_at) as last_lost_at,
    (
        extract(epoch from max(collected_at) - min(collected_at)) + 1
    )::double precision as duration_seconds,
    count(*) as lost_pings
from numbered
where lost
group by source, ping_target, outage_number
;
create view orig.link_hour_profile as
-- At which hours the link is worse (ADR-0045, decision 5): latency and loss by
-- day of the week and hour of the day, in UTC, per source and target, over
-- every ping the live table holds (about 7 days). Day of the week is ISO: 1 is
-- Monday. Percentiles are exact, over the answered pings of each cell.

select
    extract(isodow from collected_at at time zone 'UTC')::integer as day_of_week,
    extract(hour from collected_at at time zone 'UTC')::integer as hour_of_day,
    source,
    ping_target,
    count(*) as pings,
    count(*) filter (where lost) as lost_pings,
    percentile_cont(0.5) within group (order by rtt_ms) as rtt_p50_ms,
    percentile_cont(0.95) within group (order by rtt_ms) as rtt_p95_ms
from orig.stg_events__pings
group by 1, 2, 3, 4
;
create view orig.source_health_minute as
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
from orig.stg_events__health
group by 1, 2
;
create view orig.pipeline_minute as
-- The pipeline itself, per minute of event time, per source and stream of events
-- (ADR-0045, decision 5: how late an event is end to end, how much arrived
-- against one a second, and the backlog delivered after each outage).
--
-- A stream is the signal, with the target for a ping: one event a second
-- each, so a complete minute holds 60. Lateness is received_at, when the hub
-- stored the event, minus collected_at, the source's clock. An event is late
-- when it arrived more than a minute after it was collected: the backlog of
-- an outage.

with events as (
    select
        source,
        collected_at,
        received_at,
        'ping.' || ping_target as event_stream
    from orig.stg_events__pings
    union all
    select
        source,
        collected_at,
        received_at,
        'health' as event_stream
    from orig.stg_events__health
)

select
    date_trunc('minute', collected_at at time zone 'UTC') at time zone 'UTC' as minute_start,
    source,
    event_stream,
    count(*) as events,
    count(*) filter (where received_at - collected_at > interval '1 minute') as late_events,
    percentile_cont(0.5) within group (
        order by extract(epoch from received_at - collected_at)
    ) as lateness_p50_seconds,
    max(extract(epoch from received_at - collected_at))::double precision as lateness_max_seconds
from events
group by 1, 2, 3
;