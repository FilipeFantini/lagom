-- At which hours the link is worse (ADR-0045, decision 5): latency and loss by
-- day of the week and hour of the day, in UTC, per source and target, over
-- every ping the live table holds (about 7 days). Day of the week is ISO: 1 is
-- Monday. Percentiles are exact, over the answered pings of each cell: each
-- minute of streaming.ping_minute keeps its round trips (`rtts`), and the
-- cell's percentiles are taken over all of them
-- (macros/streaming/ping_minute_rows.sql). The minutes are read as
-- link_minute reads them (macros/streaming/changed_minutes.sql).
--
-- One aggregation over the minutes and their round trips: each minute is
-- joined to its round trips, one row each (a minute with none keeps one row
-- with nulls), and its pings and lost pings are counted on the first of those
-- rows only. Read once, the minutes are sorted by cell before the round trips
-- are unnested: 0.3 s over 7 days. Two aggregations joined made the planner
-- sort every round trip, about 600,000 a target, and took seconds.

with minutes as (
    {{ current_minutes(
        ref('stg_events__pings'), ref('ping_minute'), ping_minute_rows, '5 seconds',
        ['minute_start', 'source', 'ping_target', 'pings', 'lost_pings', 'rtts']
    ) }}
)

select
    extract(isodow from m.minute_start at time zone 'UTC')::integer as day_of_week,
    extract(hour from m.minute_start at time zone 'UTC')::integer as hour_of_day,
    m.source,
    m.ping_target,
    (sum(m.pings) filter (where r.n is null or r.n = 1))::bigint as pings,
    (sum(m.lost_pings) filter (where r.n is null or r.n = 1))::bigint as lost_pings,
    percentile_cont(0.5) within group (order by r.rtt_ms) as rtt_p50_ms,
    percentile_cont(0.95) within group (order by r.rtt_ms) as rtt_p95_ms
from minutes as m
left join lateral unnest(m.rtts) with ordinality as r (rtt_ms, n) on true
group by day_of_week, hour_of_day, m.source, m.ping_target
