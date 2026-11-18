-- The pipeline itself, per minute of event time, per source and stream of events
-- (ADR-0045, decision 5: how late an event is end to end, how much arrived
-- against one a second, and the backlog delivered after each outage).
--
-- A stream is the signal, with the target for a ping: one event a second
-- each, so a complete minute holds 60. Lateness is received_at, when the hub
-- stored the event, minus collected_at, the source's clock. An event is late
-- when it arrived more than a minute after it was collected: the backlog of
-- an outage. A ping stream's minutes are streaming.ping_minute's, the health
-- stream's streaming.health_minute's, each read as link_minute and
-- source_health_minute read them (macros/streaming/changed_minutes.sql).

{% set lateness = ['late_events', 'lateness_p50_seconds', 'lateness_max_seconds'] %}

with ping_minutes as (
    {{ current_minutes(
        ref('stg_events__pings'), ref('ping_minute'), ping_minute_rows, '5 seconds',
        ['minute_start', 'source', 'ping_target', 'pings'] + lateness
    ) }}
),

health_minutes as (
    {{ current_minutes(
        ref('stg_events__health'), ref('health_minute'), health_minute_rows, '0 seconds',
        ['minute_start', 'source', 'readings'] + lateness
    ) }}
)

select
    minute_start,
    source,
    'ping.' || ping_target as event_stream,
    pings as events,
    late_events,
    lateness_p50_seconds,
    lateness_max_seconds
from ping_minutes

union all

select
    minute_start,
    source,
    'health' as event_stream,
    readings as events,
    late_events,
    lateness_p50_seconds,
    lateness_max_seconds
from health_minutes
