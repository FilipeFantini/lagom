-- The link, per minute of event time, per source and target (ADR-0045,
-- decision 5: "is it good enough for a video call", and the loss to the
-- router against the loss beyond it, by target). What each column means is
-- in macros/streaming/ping_minute_rows.sql.
--
-- Read from streaming.ping_minute, with the minutes it may have wrong
-- computed again from the live table when the view is read: the latest
-- (events stored since its last build, late ones included) and the oldest
-- (trimmed by the archive). So it gives what a computation over the whole
-- live table gives, and a late event is counted by the next query
-- (macros/streaming/changed_minutes.sql).

with minutes as (
    {{ current_minutes(
        ref('stg_events__pings'), ref('ping_minute'), ping_minute_rows, '5 seconds',
        ['minute_start', 'source', 'ping_target', 'pings', 'lost_pings',
         'rtt_p50_ms', 'rtt_p95_ms', 'jitter_ms']
    ) }}
)

select
    minute_start,
    source,
    ping_target,
    pings,
    lost_pings,
    rtt_p50_ms,
    rtt_p95_ms,
    jitter_ms
from minutes
