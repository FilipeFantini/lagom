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

{% set gap = 5 %}

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
                    > interval '{{ gap }} seconds'
                )
                then 1
            else 0
        end as starts_outage
    from {{ ref('stg_events__pings') }}
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
