{#
  The streaming showcase's live table (ADR-0047, decisions 2, 4 and 5), run by
  `on-run-start` under the `serving` target only.

  Redpanda Connect inserts into it; dbt does not build it as a model because a
  model is rebuilt, and this table is the only copy of its rows outside the
  hub. So it is created once, if missing, and its columns are never altered
  here: a change to them is a migration of its own.

  - UNLOGGED: Postgres does not write it to its write-ahead log, so the
    events never reach R2 through the backup. Postgres empties it after a
    crash; the runbook rebuilds it from the hub (architecture.md, runbook).
  - The primary key on `event_id` is what stores an event delivered twice
    once: the writer's insert ignores a row whose key exists.
  - `received_at` is when the hub stored the event, from the message's
    JetStream timestamp, set by the writer. A replay from the hub then writes
    the same value again, so a table rebuilt from the hub holds the same rows.
  - `payload` is the whole event as it was published; the views read their
    fields from it.
  - Two indexes, created if missing, find the stretches of time the views
    compute again when read (macros/streaming/changed_minutes.sql): the
    events of a range of `collected_at`, and those stored after a
    `received_at`. The archive's read of an hour of `received_at` uses the
    second too. Measured locally on 2026-11-18 over 7 days of five events a
    second (3.2 million rows, the table 1,723 MB): 37 MB on `collected_at`,
    whose five events a second share one key, and 69 MB on `received_at`;
    UNLOGGED with the table. Each insert adds an entry to each, at the right
    end, since both times grow. On a table that has rows, the first build
    after they are added creates them in seconds, holding inserts back
    meanwhile; the writer waits, it does not fail.

  The writer `events_writer` (postgres-<env>/cluster.yaml) may insert, and
  nothing else: no select, no update, no delete, and no other table. The
  grants are repeated on every run, so a table created again gets them back.
#}
{% macro create_live_events() %}
    create schema if not exists live;
    create unlogged table if not exists live.events (
        event_id text primary key,
        source text not null,
        signal text not null,
        collected_at timestamp with time zone not null,
        received_at timestamp with time zone not null,
        payload jsonb not null
    );
    create index if not exists events_collected_at on live.events (collected_at);
    create index if not exists events_received_at on live.events (received_at);
    grant usage on schema live to events_writer;
    grant insert on live.events to events_writer;
{% endmacro %}
