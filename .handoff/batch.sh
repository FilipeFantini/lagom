# batch FROM TO [psql statements run on the batch before it is inserted]
# Generates the synthetic events of seconds FROM..TO into a temp table, applies
# the edits (an outage, a late arrival), then inserts them into live.events.
S=/private/tmp/claude-501/-Users-ffantini-Git-lagom/5245a278-1943-4703-9306-685a6b190349/scratchpad/m6
batch() {
  { echo "create temp table batch (like live.events);"
    cat $S/gen_batch_body.sql
    echo "${3:-}"
    echo "insert into live.events select * from batch on conflict do nothing;"
    echo "select count(*) as inserted, min(collected_at), max(collected_at), min(received_at), max(received_at) from batch;"
  } | docker exec -i lagom-m6-pg psql -U postgres -d serving -v ON_ERROR_STOP=1 -v from="$1" -v to="$2" -q -At -F ' '
}
