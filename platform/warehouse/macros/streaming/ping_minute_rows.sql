{#
  The link per minute of event time, per source and target, from a relation
  of pings with the columns of stg_events__pings (ADR-0045, decision 5). Its
  rows are `streaming.ping_minute`, and every view of `metrics` about the
  link, and the pings of `pipeline_minute`, is read from them
  (changed_minutes.sql says how they are kept).

  - **The minute** is cut from `collected_at`, the source's clock, in UTC:
    an event delivered hours late lands in its own minute. `pings` says
    whether a minute is complete (60 a target).
  - **Latency.** Percentiles are exact (percentile_cont) over the minute's
    answered pings; a minute with none has null latencies. `rtts` keeps those
    round trips, sorted, so that link_hour_profile's percentiles over an hour
    are exact too.
  - **Jitter** is the mean absolute difference between consecutive answered
    round trips to the same target (RFC 3550's idea, without its smoothing);
    a pair more than `gap` apart (lost pings or no events between them) is
    not consecutive and is left out. The pair may cross into the minute
    before.
  - **Runs of lost pings.** A lost ping starts an outage when the ping
    before it to the same target was not lost, or was more than `gap`
    earlier: with no events in between (the source down) nothing says the
    link stayed down. The minute keeps each run of its lost pings as four
    arrays, one entry a run: first and last lost ping, how many, and whether
    the run starts an outage or carries on one from the minute before.
    link_outages joins the runs into outages.
  - **Lateness** is `received_at`, when the hub stored the event, minus
    `collected_at`. An event is late when it arrived more than a minute after
    it was collected.
  - `max_received_at` is how far a build of the table read (changed_minutes.sql).

  The window functions read the relation given, so a caller that gives only
  a stretch of time must give the `gap` before it too.
#}
{% macro ping_minute_rows(pings) %}
{% set gap = '5 seconds' %}
    with minute_pings as (
        select
            source,
            ping_target,
            collected_at,
            received_at,
            lost,
            rtt_ms,
            date_trunc('minute', collected_at at time zone 'UTC') at time zone 'UTC' as minute_start,
            lost and (
                not coalesce(lag(lost) over by_target, false)
                or collected_at - lag(collected_at) over by_target > interval '{{ gap }}'
            ) as starts_outage
        from {{ pings }}
        window by_target as (partition by source, ping_target order by collected_at)
    ),

    answered as (
        select
            minute_start,
            source,
            ping_target,
            collected_at,
            rtt_ms,
            lag(rtt_ms) over by_target as previous_rtt_ms,
            lag(collected_at) over by_target as previous_at
        from minute_pings
        where not lost
        window by_target as (partition by source, ping_target order by collected_at)
    ),

    jitter as (
        select
            minute_start,
            source,
            ping_target,
            avg(abs(rtt_ms - previous_rtt_ms)) as jitter_ms
        from answered
        where collected_at - previous_at <= interval '{{ gap }}'
        group by minute_start, source, ping_target
    ),

    -- Within a minute, a run is numbered by the outages started before it:
    -- run 0 carries on an outage from the minute before.
    lost_pings as (
        select
            minute_start,
            source,
            ping_target,
            collected_at,
            starts_outage,
            count(*) filter (where starts_outage) over (
                partition by source, ping_target, minute_start order by collected_at
                rows between unbounded preceding and current row
            ) as run_in_minute
        from minute_pings
        where lost
    ),

    runs as (
        select
            minute_start,
            source,
            ping_target,
            min(collected_at) as started_at,
            max(collected_at) as last_lost_at,
            count(*) as lost_pings,
            bool_or(starts_outage) as starts_outage
        from lost_pings
        group by minute_start, source, ping_target, run_in_minute
    ),

    minute_runs as (
        select
            minute_start,
            source,
            ping_target,
            array_agg(started_at order by started_at) as run_started_at,
            array_agg(last_lost_at order by started_at) as run_last_lost_at,
            array_agg(lost_pings order by started_at) as run_lost_pings,
            array_agg(starts_outage order by started_at) as run_starts_outage
        from runs
        group by minute_start, source, ping_target
    ),

    per_minute as (
        select
            minute_start,
            source,
            ping_target,
            count(*) as pings,
            count(*) filter (where lost) as lost_pings,
            percentile_cont(0.5) within group (order by rtt_ms) as rtt_p50_ms,
            percentile_cont(0.95) within group (order by rtt_ms) as rtt_p95_ms,
            array_agg(rtt_ms order by rtt_ms) filter (where rtt_ms is not null) as rtts,
            count(*) filter (where received_at - collected_at > interval '1 minute') as late_events,
            percentile_cont(0.5) within group (
                order by extract(epoch from received_at - collected_at)
            ) as lateness_p50_seconds,
            max(extract(epoch from received_at - collected_at))::double precision
                as lateness_max_seconds,
            max(received_at) as max_received_at
        from minute_pings
        group by minute_start, source, ping_target
    )

    select
        m.minute_start,
        m.source,
        m.ping_target,
        m.pings,
        m.lost_pings,
        m.rtt_p50_ms,
        m.rtt_p95_ms,
        j.jitter_ms,
        m.rtts,
        r.run_started_at,
        r.run_last_lost_at,
        r.run_lost_pings,
        r.run_starts_outage,
        m.late_events,
        m.lateness_p50_seconds,
        m.lateness_max_seconds,
        m.max_received_at
    from per_minute as m
    left join jitter as j
        on m.minute_start = j.minute_start and m.source = j.source and m.ping_target = j.ping_target
    left join minute_runs as r
        on m.minute_start = r.minute_start and m.source = r.source and m.ping_target = r.ping_target
{% endmacro %}
