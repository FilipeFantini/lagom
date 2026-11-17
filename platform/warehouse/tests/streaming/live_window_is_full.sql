{#
  What makes an emptied or a stalled live table loud (ADR-0047, decision 5).

  Postgres empties `live.events` after a crash, since it is UNLOGGED, and a
  writer that stops leaves it to go stale. Neither fails anything by itself:
  the views would just show less. This test fails when, over the last 60
  whole minutes of event time (less the last two, which may still be in
  flight), any of the five streams holds fewer than half the events one a
  second would give: 1,800 of 3,600. The `streaming` job runs it on a
  schedule, and its failure raises the environment's alarm (ADR-0033).

  It reads pipeline_minute, not the table, so it runs after the views are
  built and a failure stops none of them. It also fails during an outage of
  the source's link, and for up to an hour after a rebuild from the hub:
  both are what it should say.
#}
with streams (event_stream) as (
    values ('health'), ('ping.gateway'), ('ping.resolver-a'), ('ping.resolver-b'), ('ping.node')
),

counted as (
    select
        event_stream,
        sum(events) as events
    from {{ ref('pipeline_minute') }}
    where
        minute_start >= date_trunc('minute', now()) - interval '62 minutes'
        and minute_start < date_trunc('minute', now()) - interval '2 minutes'
    group by event_stream
)

select
    s.event_stream,
    coalesce(c.events, 0) as events_last_hour
from streams as s
left join counted as c on s.event_stream = c.event_stream
where coalesce(c.events, 0) < 1800
