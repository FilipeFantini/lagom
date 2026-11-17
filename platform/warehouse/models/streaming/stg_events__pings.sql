-- One row per ping result, typed from the event's payload (ADR-0045,
-- decision 4). A lost ping is an event too, with no round trip: its rtt_ms is
-- null here whatever the payload says, so a producer that writes 0 or -1 for
-- a lost reply cannot pull the percentiles down. Read only by the views in
-- metrics/.

select
    event_id,
    source,
    payload ->> 'target' as ping_target,
    collected_at,
    received_at,
    (payload ->> 'lost')::boolean as lost,
    case
        when not (payload ->> 'lost')::boolean then (payload ->> 'rtt_ms')::double precision
    end as rtt_ms
from {{ source('live', 'events') }}
where signal = 'ping'
