-- Synthetic events at the staging producer's rate and shape (connect-config.yaml,
-- synthetic.blobl): per second one health reading and four pings; the outage at
-- minutes 7 and 37 (first 20 s, every target beyond the gateway); plus 0.3 %
-- random loss. received_at is collected_at plus 0.1-0.9 s.
-- :from and :to are the first and last second, as timestamptz literals.
insert into live.events (event_id, source, signal, collected_at, received_at, payload)
select
    'synthetic/' || kind || '/' || to_char(at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
    'synthetic',
    case when kind = 'health' then 'health' else 'ping' end,
    at,
    at + (0.1 + random() * 0.8) * interval '1 second',
    case when kind = 'health' then jsonb_build_object(
        'source', 'synthetic', 'signal', 'health', 'seq', seq,
        'collected_at', to_char(at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
        'event_id', 'synthetic/health/' || to_char(at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
        'cpu_temp_c', 45 + floor(random() * 151) / 10, 'cpu_freq_mhz', case when random() < 0.3 then 1400 else 600 end,
        'throttled', false, 'under_voltage', false, 'load_1m', floor(random() * 201) / 100,
        'mem_used_bytes', 300000000 + floor(random() * 50000000)::bigint, 'mem_total_bytes', 953000000,
        'disk_used_bytes', 2300000000 + seq * 10, 'disk_total_bytes', 13800000000)
    else jsonb_build_object(
        'source', 'synthetic', 'signal', 'ping', 'seq', seq, 'target', substr(kind, 6),
        'collected_at', to_char(at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
        'event_id', 'synthetic/' || kind || '/' || to_char(at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
        'lost', lost, 'rtt_ms', case when lost then null else base + floor(random() * 200) / 10 end)
    end
from (
    select at, seq, kind, base,
        kind <> 'ping.gateway' and (
            (extract(minute from at)::int % 30 = 7 and extract(second from at) < 20) or random() < 0.003
        ) as lost
    from (
        select at, row_number() over (order by at) as seq
        from generate_series(:'from'::timestamptz, :'to'::timestamptz, interval '1 second') as at
    ) as s
    cross join (values ('health', 0), ('ping.gateway', 1), ('ping.resolver-a', 10),
                       ('ping.resolver-b', 12), ('ping.node', 30)) as k (kind, base)
) as e
on conflict do nothing;
