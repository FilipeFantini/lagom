"""Time every query of the streaming dashboard over a 7-day range, as grafana_reader.

Reads the dashboard ConfigMap, replaces Grafana's macros, and runs each query
with EXPLAIN (ANALYZE, TIMING OFF) through `docker exec psql`, `runs` times.
"""

import json
import re
import statistics
import subprocess
import sys

dashboard_path, runs = sys.argv[1], int(sys.argv[2])
text = open(dashboard_path).read()
body = text.split("streaming.json: |", 1)[1]
board = json.loads("\n".join(line[4:] for line in body.splitlines()))

queries = [("source variable", board["templating"]["list"][1]["query"])]
for panel in board["panels"]:
    for target in panel["targets"]:
        queries.append((f"{panel['title']} [{target['refId']}]", target["rawSql"]))


def expand(sql: str) -> str:
    sql = re.sub(
        r"\$__timeGroupAlias\((\w+), \$__interval\)",
        r"date_bin('10 minutes', \1, timestamptz '2000-01-01') as time",
        sql,
    )
    sql = re.sub(r"\$__timeFilter\((\w+)\)", r"\1 between now() - interval '7 days' and now()", sql)
    sql = sql.replace("$__interval_ms", "600000").replace("$source", "synthetic")
    return sql.replace("$target", "node")


for title, sql in queries:
    times = []
    for _ in range(runs):
        out = subprocess.run(
            ["docker", "exec", "-i", "lagom-m6-pg", "psql", "-U", "postgres", "-d", "serving",
             "-v", "ON_ERROR_STOP=1", "-At"],
            input=f"set role grafana_reader;\nexplain (analyze, timing off) {expand(sql)};\n",
            capture_output=True, text=True, check=True,
        ).stdout
        times.append(float(re.search(r"Execution Time: ([\d.]+) ms", out).group(1)))
    print(f"{statistics.median(times):9.1f} ms  (min {min(times):.1f}, max {max(times):.1f})  {title}")
