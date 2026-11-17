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
    from {{ ref('stg_events__pings') }}
    union all
    select
        source,
        collected_at,
        received_at,
        'health' as event_stream
    from {{ ref('stg_events__health') }}
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
