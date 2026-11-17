-- One row per health reading of the source, typed from the event's payload
-- (ADR-0045, decision 4). Read only by the views in metrics/.

select
    event_id,
    source,
    collected_at,
    received_at,
    (payload ->> 'cpu_temp_c')::double precision as cpu_temp_c,
    (payload ->> 'cpu_freq_mhz')::double precision as cpu_freq_mhz,
    (payload ->> 'throttled')::boolean as throttled,
    (payload ->> 'under_voltage')::boolean as under_voltage,
    (payload ->> 'load_1m')::double precision as load_1m,
    (payload ->> 'mem_used_bytes')::bigint as mem_used_bytes,
    (payload ->> 'mem_total_bytes')::bigint as mem_total_bytes,
    (payload ->> 'disk_used_bytes')::bigint as disk_used_bytes,
    (payload ->> 'disk_total_bytes')::bigint as disk_total_bytes
from {{ source('live', 'events') }}
where signal = 'health'
