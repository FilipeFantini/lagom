set -u
S=/private/tmp/claude-501/-Users-ffantini-Git-lagom/5245a278-1943-4703-9306-685a6b190349/scratchpad/m6
W=/Users/ffantini/Git/lagom/.claude/worktrees/m6-incremental-minutes
source $S/../env.sh
echo "#### fresh database, CI's serving job"
docker exec -i lagom-m6-pg psql -U postgres -q -c "drop database serving with (force)" -c "create database serving owner serving"
dbtw build --exclude live_window_is_full 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | grep -E "Done\."
if dbtw test --select live_window_is_full >/dev/null 2>&1; then echo "live_window_is_full PASSED on empty (unexpected)"; else echo "live_window_is_full fails on an empty table, as it must"; fi
(cd $W && LAGOM_ARCHIVE_TEST_PG="host=localhost port=55432 dbname=serving user=serving password=ci-placeholder sslmode=disable" uv run --project platform/orchestration --locked pytest platform/orchestration/tests/test_archive.py -q 2>&1 | tail -1)
echo "#### 7 days and an hour of events, full build, against main's SQL"
pg -q -c "truncate live.events"
pg -v from="2026-11-11 08:40:29+00" -v to="2026-11-18 09:40:24+00" < $S/generate.sql && pg -q -c "vacuum analyze live.events"
dbtw build --exclude live_window_is_full 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | grep -E "incremental model|minutes_equal|Done\." | grep -v START | sed -E 's/^[0-9:]+ +//; s/ \.+ / /'
pg -q < $S/orig_views.sql; pg -At -F ' ' < $S/compare.sql
echo "#### scenario"
bash $S/scenario.sh 2>&1 | grep -v -E "NOTICE|DETAIL|drop cascades|START test"
echo "#### timings: an hour stored since the last build"
source $S/batch.sh
batch "2026-11-18 12:30:01+00" "2026-11-18 13:30:00+00" ""; pg -q -c "vacuum analyze live.events"
(cd $W && python3 -I $S/panels_at.py platform/infra/grafana/dashboard-streaming.yaml 5)
echo "#### timings: right after a 6-hour backlog"
batch "2026-11-18 13:30:01+00" "2026-11-18 19:30:00+00" "update batch set received_at = timestamptz '2026-11-18 19:30:00+00' + (collected_at - timestamptz '2026-11-18 13:30:00+00') / 3600;"; pg -q -c "vacuum analyze live.events"
(cd $W && python3 -I $S/panels_backlog.py platform/infra/grafana/dashboard-streaming.yaml 3)
dbtw build --exclude live_window_is_full 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | grep -E "incremental model|minutes_equal|Done\." | grep -v START | sed -E 's/^[0-9:]+ +//; s/ \.+ / /'
pg -At -F ' ' < $S/compare.sql | head -5 | tr '\n' ';'; echo
pg -c "select c.relname, c.reltuples::bigint as rows, pg_size_pretty(pg_total_relation_size(c.oid)) as size, c.relpersistence from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname in ('streaming','live') and c.relkind in ('r','i') order by 1"
echo "#### done"
