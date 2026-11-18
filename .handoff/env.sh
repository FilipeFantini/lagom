export LAGOM_PG_HOST=localhost LAGOM_PG_PORT=55432 LAGOM_PG_USER=serving LAGOM_PG_PASSWORD=ci-placeholder LAGOM_PG_DBNAME=serving LAGOM_PG_SSLMODE=disable
W=/Users/ffantini/Git/lagom/.claude/worktrees/m6-incremental-minutes
dbtw() { (cd $W && uv run --project platform/orchestration --locked dbt "$@" --target serving --no-partial-parse --project-dir platform/warehouse --profiles-dir platform/warehouse); }
pg() { docker exec -i lagom-m6-pg psql -U postgres -d serving -v ON_ERROR_STOP=1 "$@"; }
