set -u
S=/private/tmp/claude-501/-Users-ffantini-Git-lagom/5245a278-1943-4703-9306-685a6b190349/scratchpad/m6
source $S/../env.sh; source $S/batch.sh
D=2026-11-18
lost_b="update batch set payload = payload || '{\"lost\": true, \"rtt_ms\": null}' where signal = 'ping' and payload->>'target' = 'resolver-b' and collected_at >= '$D 09:45:00+00' and collected_at < '$D 11:30:00+00';"
compare() { echo "-- views against the original SQL ($1):"; pg -At -F ' ' < $S/compare.sql | head -5 | tr '\n' ';'; echo; }
build() { echo "-- dbt build ($1):"; dbtw build --exclude live_window_is_full "${@:2}" 2>&1 | grep -E "minutes_equal_a_full|Done\." | sed 's/\x1b\[[0-9;]*m//g' | sed -E 's/^[0-9:]+ +//'; }
outage() { pg -At -F ' ' -c "select started_at, last_lost_at, lost_pings from metrics.link_outages where ping_target = 'resolver-b' and started_at >= '$D 09:40+00' order by 1"; }

echo "== A1: 09:40:25-11:00:00 arrive on time; resolver-b lost from 09:45 to 11:30; 09:50-09:52 held back (the Pi offline)"
batch "$D 09:40:25+00" "$D 11:00:00+00" "$lost_b delete from batch where collected_at >= '$D 09:50:00+00' and collected_at < '$D 09:52:00+00';"
compare "A1, before the build"; build "A1"; compare "A1, after the build"; outage
echo "== A2: 11:00:01-11:45:00 arrive on time; the outage carries on across the stretch the last build trusts"
batch "$D 11:00:01+00" "$D 11:45:00+00" "$lost_b"
compare "A2, before the build"; build "A2"; compare "A2, after the build"; outage
echo "== B: the held-back 09:50-09:52 arrive late, received two hours after collection"
batch "$D 09:50:00+00" "$D 09:51:59+00" "$lost_b update batch set received_at = collected_at + interval '2 hours';"
compare "B, before the build"; outage; build "B"; compare "B, after the build"; outage
echo "== C: the archive trims the oldest hour of received_at"
pg -At -c "delete from live.events where received_at < (select date_trunc('hour', min(received_at)) + interval '1 hour' from live.events)" -c "select min(received_at), min(collected_at), count(*) from live.events"
compare "C, before the build"; build "C"; compare "C, after the build"
echo "== D: rows of a middle hour deleted by hand: the guard must fail"
pg -At -c "delete from live.events where collected_at >= '2026-11-14 12:00+00' and collected_at < '2026-11-14 12:10+00'"
build "D, expected to fail the guard"
compare "D, after the build (expected to differ)"
echo "== E: --full-refresh repairs it"
build "E, full refresh" --full-refresh; compare "E, after the full refresh"
echo "== F: a second run changes nothing, and incremental equals full refresh"
batch "$D 11:45:01+00" "$D 12:30:00+00" ""
build "F1, incremental"
snap() { pg -q -c "drop schema if exists snap cascade" -c "create schema snap" -c "create table snap.ping_minute as select * from streaming.ping_minute" -c "create table snap.health_minute as select * from streaming.health_minute"; }
snap; build "F2, the same run again"
echo "-- tables after F2 against after F1:"; pg -At -F ' ' < $S/state_diff.sql
build "F3, full refresh" --full-refresh
echo "-- tables after F3 (full refresh) against after F2 (incremental):"; pg -At -F ' ' < $S/state_diff.sql
compare "F, after the full refresh"
