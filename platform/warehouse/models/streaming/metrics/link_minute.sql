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
-- trips to the same target (RFC 3550's idea, without its smoothing).
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
    from {{ ref('stg_events__pings') }}
),

steps as (
    select
        minute_start,
        source,
        ping_target,
        abs(
            rtt_ms - lag(rtt_ms) over (partition by source, ping_target order by collected_at)
        ) as rtt_step_ms
    from pings
    where not lost
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
