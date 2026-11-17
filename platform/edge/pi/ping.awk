# Linux iputils ping, LC_ALL=C. Output is an allowlist, never the raw line.
# collected_at is the observation time (reply or timeout), not the send time.
/^\[[0-9]+\.[0-9]+\]/ {
    lost = ($0 ~ /no answer yet for icmp_seq=/)
    if ($0 ~ / From / && $0 ~ /icmp_seq=/) lost = 1
    if (!lost && $0 !~ / bytes from /) {
        print "Unknown timestamped ping result" > "/dev/stderr"; exit 1
    }
    timestamp = $1
    gsub(/[\[\]]/, "", timestamp)
    seq = ""; rtt = ""
    for (i = 2; i <= NF; i++) {
        if ($i ~ /^icmp_seq=/) { seq = $i; sub(/^icmp_seq=/, "", seq) }
        if ($i ~ /^time=/) { rtt = $i; sub(/^time=/, "", rtt) }
    }
    if (seq !~ /^[0-9]+$/ || (!lost && rtt !~ /^[0-9]+(\.[0-9]+)?$/)) {
        print "Malformed ping result" > "/dev/stderr"; exit 1
    }
    # A reply arriving after its timeout does not become a second observation.
    # Entries expire, allowing the 16-bit ICMP sequence to wrap on long runs.
    if (seq in seen && timestamp - seen[seq] < 60) next
    seen[seq] = timestamp
    # Keep this at most 128 entries, instead of growing with the service uptime.
    delete seen[(seq + 65536 - 128) % 65536]
    printf "{\"signal\":\"ping\",\"target\":\"%s\",\"observed_at\":%s,\"lost\":%s,\"rtt_ms\":%s}\n", target, timestamp, lost ? "true" : "false", lost ? "null" : rtt
    fflush()
}
