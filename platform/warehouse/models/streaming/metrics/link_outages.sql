-- Outages of the link per source and target (ADR-0045, decision 5: "how long
-- was it down"): a run of consecutive lost pings to one target, in event time.
--
-- A lost ping starts an outage when the ping before it to the same target was
-- not lost, or was more than five seconds earlier: with no events in between
-- (the source down) nothing says the link stayed down, so two runs either
-- side of the gap stay two. Each minute of streaming.ping_minute keeps its
-- runs of lost pings and whether each starts an outage
-- (macros/streaming/ping_minute_rows.sql); the running count of starts, over
-- every run of the live table, numbers the outages, so an outage that spans
-- many minutes, or hours, is one outage. A ping that arrives late is placed
-- by its collected_at, and the minutes it touches are computed again when
-- the view is read (macros/streaming/changed_minutes.sql). A single lost
-- ping is an outage of one second: the reader filters by duration.
--
-- The duration is from the first lost ping to the last, plus the one-second
-- sampling interval.

with minutes as (
    {{ current_minutes(
        ref('stg_events__pings'), ref('ping_minute'), ping_minute_rows, '5 seconds',
        ['minute_start', 'source', 'ping_target', 'run_started_at', 'run_last_lost_at',
         'run_lost_pings', 'run_starts_outage']
    ) }}
),

runs as (
    select
        m.source,
        m.ping_target,
        r.started_at,
        r.last_lost_at,
        r.lost_pings,
        r.starts_outage
    from minutes as m
    cross join lateral unnest(
        m.run_started_at, m.run_last_lost_at, m.run_lost_pings, m.run_starts_outage
    ) as r (started_at, last_lost_at, lost_pings, starts_outage)
),

numbered as (
    select
        source,
        ping_target,
        started_at,
        last_lost_at,
        lost_pings,
        count(*) filter (where starts_outage) over (
            partition by source, ping_target order by started_at
            rows between unbounded preceding and current row
        ) as outage_number
    from runs
)

select
    source || '/' || ping_target || '/' || to_char(
        min(started_at) at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
    ) as outage_id,
    source,
    ping_target,
    min(started_at) as started_at,
    max(last_lost_at) as last_lost_at,
    (
        extract(epoch from max(last_lost_at) - min(started_at)) + 1
    )::double precision as duration_seconds,
    sum(lost_pings)::bigint as lost_pings
from numbered
group by source, ping_target, outage_number
