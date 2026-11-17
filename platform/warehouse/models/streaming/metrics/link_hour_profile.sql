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
from {{ ref('stg_events__pings') }}
group by 1, 2, 3, 4
