-- One row per ping result, typed from the event's payload (ADR-0045,
-- decision 4). A lost ping is an event too, with no round trip. Read only by
-- the views in metrics/.

select
    event_id,
    source,
    payload ->> 'target' as ping_target,
    collected_at,
    received_at,
    (payload ->> 'lost')::boolean as lost,
    (payload ->> 'rtt_ms')::double precision as rtt_ms
from {{ source('live', 'events') }}
where signal = 'ping'
