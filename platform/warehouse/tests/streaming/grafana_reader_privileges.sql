{#
  grafana_reader may read the views of `metrics` and nothing else of
  `serving`, may write nowhere, and holds its limits (ADR-0047, decision 7).
  Returns a row, and so fails, for each thing that is not so:

  - a relation of `metrics`, `streaming` or `live` it can write, or whose
    SELECT is not exactly "in `metrics`";
  - USAGE on `metrics` missing, or USAGE on `streaming` or `live` present;
  - CREATE on the database, on any of the three schemas or on `public`;
  - no connection limit, or no statement timeout, on the role. The timeout
    is set by hand (runbook: The streaming showcase in staging), so this is
    what makes its loss loud.

  The grants come from dbt_project.yml; this checks the outcome in the
  database, not the config. The refs make dbt build every view of `metrics`
  before it runs.
#}
-- depends_on: {{ ref('link_minute') }}
-- depends_on: {{ ref('link_hour_profile') }}
-- depends_on: {{ ref('link_outages') }}
-- depends_on: {{ ref('source_health_minute') }}
-- depends_on: {{ ref('pipeline_minute') }}

with relations as (
    select
        table_schema,
        table_name,
        has_table_privilege(
            'grafana_reader', format('%I.%I', table_schema, table_name), 'SELECT'
        ) as can_read,
        has_table_privilege(
            'grafana_reader',
            format('%I.%I', table_schema, table_name),
            'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER'
        ) as can_write
    from information_schema.tables
    where table_schema in ('metrics', 'streaming', 'live')
),

schemas (schema_name, usage_expected) as (
    values ('metrics', true), ('streaming', false), ('live', false)
),

role as (
    select
        rolconnlimit,
        rolconfig
    from pg_roles
    where rolname = 'grafana_reader'
)

select
    table_schema as what,
    table_name as detail
from relations
where can_write or can_read <> (table_schema = 'metrics')

union all

select
    schema_name as what,
    'usage is not ' || usage_expected::text as detail
from schemas
where has_schema_privilege('grafana_reader', schema_name, 'USAGE') <> usage_expected

union all

select
    schema_name as what,
    'create' as detail
from (values ('metrics'), ('streaming'), ('live'), ('public')) as s (schema_name)
where has_schema_privilege('grafana_reader', schema_name, 'CREATE')

union all

select
    'database' as what,
    'create' as detail
where has_database_privilege('grafana_reader', current_database(), 'CREATE')

union all

select
    'role' as what,
    'missing, no connection limit or no statement timeout' as detail
where not exists (
    select 1
    from role
    where
        rolconnlimit > 0
        and exists (
            select 1 from unnest(rolconfig) as setting
            where setting like 'statement\_timeout=%' and setting <> 'statement_timeout=0'
        )
)
