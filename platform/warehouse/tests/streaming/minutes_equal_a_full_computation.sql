{#
  The minutes the views of `metrics` read are what a computation over the
  whole live table gives (ADR-0047, decision 8; macros/streaming/
  changed_minutes.sql). Returns a row, and so fails, for every minute that
  one side has and the other has not, or has with another value, in each of
  the two tables of minutes. The five views are functions of these minutes,
  so they are then what the views would be if computed over the whole table.

  It is what makes a broken assumption of the incremental tables loud: an
  event stored by the writer more than an hour after a newer one, a source
  clock more than an hour ahead, a row of `live.events` deleted by hand, or
  a rebuild from the hub without emptying the tables (runbook). Each side
  is one query, so both read the same rows of the live table, whatever the
  writer inserts meanwhile.

  Averages are compared to nine decimals: a sum of floating-point numbers
  depends on the order it is taken in, which the query plan chooses, so the
  same minute can differ in its last bits. Everything else is compared
  exactly. It reads the whole live table, as the views did before they read
  the tables of minutes: about 20 seconds a run at 7 days, in the job, not
  in a panel.

  The refs below make it run after the five views. A failing test skips
  what is built after the models it reads, and each build creates the
  views of `streaming` again with `drop ... cascade`, which drops the views
  of `metrics`; without the refs, a failure here would leave some of them
  missing until the next build.
#}
-- depends_on: {{ ref('link_minute') }}
-- depends_on: {{ ref('link_hour_profile') }}
-- depends_on: {{ ref('link_outages') }}
-- depends_on: {{ ref('source_health_minute') }}
-- depends_on: {{ ref('pipeline_minute') }}

{% set ping_columns = [
    'minute_start', 'source', 'ping_target', 'pings', 'lost_pings', 'rtt_p50_ms',
    'rtt_p95_ms', 'jitter_ms', 'rtts', 'run_started_at', 'run_last_lost_at',
    'run_lost_pings', 'run_starts_outage', 'late_events', 'lateness_p50_seconds',
    'lateness_max_seconds'
] %}
{% set health_columns = [
    'minute_start', 'source', 'readings', 'cpu_temp_max_c', 'cpu_temp_avg_c',
    'cpu_freq_avg_mhz', 'load_1m_avg', 'throttled_seconds', 'under_voltage_seconds',
    'disk_used_bytes', 'disk_total_bytes', 'late_events', 'lateness_p50_seconds',
    'lateness_max_seconds'
] %}
{# Averages to nine decimals, everything else as it is. #}
{% set averages = ['jitter_ms', 'cpu_temp_avg_c', 'cpu_freq_avg_mhz', 'load_1m_avg'] %}
{% set compared = {'ping': [], 'health': []} %}
{% for kind, columns in [('ping', ping_columns), ('health', health_columns)] %}
    {% for c in columns %}
        {% set rounded = 'round(' ~ c ~ '::numeric, 9) as ' ~ c %}
        {% do compared[kind].append(rounded if c in averages else c) %}
    {% endfor %}
{% endfor %}

with read_pings as (
    {{ current_minutes(
        ref('stg_events__pings'), ref('ping_minute'), ping_minute_rows, '5 seconds', ping_columns
    ) }}
),

full_pings as (
    {{ ping_minute_rows(ref('stg_events__pings')) }}
),

read_health as (
    {{ current_minutes(
        ref('stg_events__health'), ref('health_minute'), health_minute_rows, '0 seconds',
        health_columns
    ) }}
),

full_health as (
    {{ health_minute_rows(ref('stg_events__health')) }}
),

ping_differences as (
    (
        select 'read, not computed' as side, {{ compared['ping'] | join(', ') }} from read_pings
        except all
        select 'read, not computed' as side, {{ compared['ping'] | join(', ') }} from full_pings
    )
    union all
    (
        select 'computed, not read' as side, {{ compared['ping'] | join(', ') }} from full_pings
        except all
        select 'computed, not read' as side, {{ compared['ping'] | join(', ') }} from read_pings
    )
),

health_differences as (
    (
        select 'read, not computed' as side, {{ compared['health'] | join(', ') }} from read_health
        except all
        select 'read, not computed' as side, {{ compared['health'] | join(', ') }} from full_health
    )
    union all
    (
        select 'computed, not read' as side, {{ compared['health'] | join(', ') }} from full_health
        except all
        select 'computed, not read' as side, {{ compared['health'] | join(', ') }} from read_health
    )
)

select
    'ping_minute' as minutes,
    side,
    minute_start,
    source,
    ping_target as detail
from ping_differences

union all

select
    'health_minute' as minutes,
    side,
    minute_start,
    source,
    null::text as detail
from health_differences
