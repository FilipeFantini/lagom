{#
  How the streaming showcase keeps its minutes (ADR-0047, decision 8, its
  second remedy). Each kind of event has a table of minutes in `streaming`
  (`ping_minute`, `health_minute`): one row per minute of `collected_at`
  (UTC), built by a dbt incremental model in the hourly `streaming` job. The
  views of `metrics` read that table, and compute again from `live.events`
  only the two stretches of event time the table may have wrong:

  - **The recent stretch**, from `recent_from` on. The table's newest
    `received_at` is how far the last build read; every event the hub stored
    less than `writer_margin` before it, or after, is taken as new, and the
    stretch starts at the minute of the earliest such event's `collected_at`.
    A late event (an old `collected_at`, a new `received_at`) moves it back
    to its own minute, so the minutes it belongs to are computed again. The
    margin covers a writer that stores an event after a newer one: the hub
    sets `received_at`, the writer inserts it about a second later, and after
    a stop of the writer, much later.
  - **The trimmed stretch**, before `trimmed_before`. The archive deletes
    whole hours of `received_at` (archive.py), so every event older than the
    live table's oldest `received_at` is gone, and its `collected_at` is at
    most `clock_margin` later than that: the source's clock is not taken to
    run more than an hour ahead of the hub's. Two minutes more cover the
    minute after the last one that lost an event, whose jitter or outage may
    have read it.

  Between the two, every minute's events and the events before it (`context`
  of them: the lag a minute's first ping reads) are the same as when the
  table was built, so the table's row is what a computation over the whole
  live table would give. The stretches are computed over the events of
  `collected_at` at or after `recent_from` minus `context`, and before
  `trimmed_before`, which an index on `collected_at` finds; the window
  functions run after that filter, so a query reads about two hours of
  events, not seven days.

  If an assumption breaks (the writer stores an event more than an hour
  older than the newest it stored, the source's clock runs more than an hour
  ahead, a row of `live.events` is deleted by hand, or the table is rebuilt
  from the hub), a minute can be wrong and stay wrong.
  tests/streaming/minutes_equal_a_full_computation.sql compares, every
  hour, what the views read with a computation over the whole live table,
  and fails on any difference; the runbook's rebuild empties the tables of
  minutes.

  `events` is a relation of the events with `collected_at` and
  `received_at`; `state` the table of minutes, with `minute_start` and
  `max_received_at`; `rows` the macro that computes the minutes of a
  relation of events; `context` an interval, as text.
#}

{# The minute before which the table is not trusted: the trimmed stretch. #}
{% macro trimmed_before(events) -%}
    {%- set clock_margin = '1 hour' -%}
    coalesce(
        (
            select
                (
                    date_trunc('minute', (min(received_at) + interval '{{ clock_margin }}') at time zone 'UTC')
                    at time zone 'UTC'
                ) + interval '2 minutes'
            from {{ events }}
        ),
        -- No event at all: the table is trusted nowhere.
        'infinity'::timestamptz
    )
{%- endmacro %}

{#
  The minute from which the table is not trusted: the recent stretch.

  The bounds are only known when the query runs, so the planner cannot use
  the statistics to guess how many events they select, and guesses a third
  of the table for `x > bound`, enough to read all of it. A range with two
  ends, `x > bound and x <= 'infinity'`, it guesses at half a percent, and
  reads the index. `min()` over an indexed column would be read from that
  index instead, walking it from the oldest event; the materialized CTE
  keeps it on the index of `received_at`.
#}
{% macro recent_from(events, state) -%}
    {%- set writer_margin = '1 hour' -%}
    coalesce(
        (
            with recent as materialized (
                select collected_at
                from {{ events }}
                where
                    received_at > (select max(max_received_at) from {{ state }})
                    - interval '{{ writer_margin }}'
                    and received_at <= 'infinity'::timestamptz
            )

            select date_trunc('minute', min(collected_at) at time zone 'UTC') at time zone 'UTC'
            from recent
        ),
        case
            -- Nothing stored since: every minute after the trimmed stretch is trusted.
            when exists (select from {{ state }}) then 'infinity'::timestamptz
            -- An empty table of minutes: nothing is trusted.
            else '-infinity'::timestamptz
        end
    )
{%- endmacro %}

{#
  The two stretches, computed again: `bounds` holds where they end and
  start, `changed_events` the events they read (theirs, and `context` before
  the recent one, which the first minute's lag reads), and `recomputed`
  their minutes. The filter is written as ranges with two ends, for the
  planner's guess explained above `recent_from`.
#}
{% macro stretches(events, state, rows, context) %}
    bounds as (
        select
            {{ trimmed_before(events) }} as trimmed_before,
            {{ recent_from(events, state) }} as recent_from
    ),

    changed_events as (
        select *
        from {{ events }}
        where
            (
                collected_at >= '-infinity'::timestamptz
                and collected_at < (select trimmed_before from bounds)
            )
            or (
                collected_at >= (select recent_from from bounds) - interval '{{ context }}'
                and collected_at <= 'infinity'::timestamptz
            )
    ),

    recomputed as (
        {{ rows('changed_events') }}
    )
{% endmacro %}

{# The minutes as a computation over the whole live table would give them, now. #}
{% macro current_minutes(events, state, rows, context, columns) %}
    with {{ stretches(events, state, rows, context) }}

    select {{ columns | join(', ') }}
    from {{ state }}
    where
        minute_start >= (select trimmed_before from bounds)
        and minute_start < (select recent_from from bounds)

    union all

    select {{ columns | join(', ') }}
    from recomputed
    where
        minute_start < (select trimmed_before from bounds)
        or minute_start >= (select recent_from from bounds)
{% endmacro %}

{#
  An incremental build: the minutes of both stretches, computed again. They
  replace the table's rows of the same keys (`delete+insert`); the trimmed
  stretch's rows are deleted first by `delete_trimmed_minutes`, since a
  minute whose every event was archived has no row here to replace it.
#}
{% macro changed_minutes(events, state, rows, context) %}
    with {{ stretches(events, state, rows, context) }}

    select *
    from recomputed
    where
        minute_start < (select trimmed_before from bounds)
        or minute_start >= (select recent_from from bounds)
{% endmacro %}

{#
  The pre-hook of an incremental build: the table's rows of the trimmed
  stretch, deleted. It runs in the build's transaction, before the minutes
  are computed, so no reader sees the table without them. It can only lower
  the table's newest `received_at`, which moves `recent_from` back: more is
  computed again, never less.
#}
{% macro delete_trimmed_minutes(events) %}
    {% if is_incremental() %}
        delete from {{ this }} where minute_start < {{ trimmed_before(events) }}
    {% endif %}
{% endmacro %}
