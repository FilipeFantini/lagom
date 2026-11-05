# ADR-0027 — WAL archived hourly, prod backups kept 14 days

- **Status:** Accepted
- **Date:** 2026-11-05
- **Supersedes:** in [ADR-0025](0025-postgres-backup-barman-cloud-to-r2.md),
  the RPO of minutes and prod's 30-day retention. The plugin, the buckets, the
  daily base backup and staging's 7 days stand.

## Context and problem

Each environment backs up Postgres to its own R2 bucket (ADR-0025). Eight days
in, each backup bucket held about 1 GB. Little real data had been ingested
yet. R2's free tier is 10 GB in total, across every bucket of the account,
the DuckLake buckets included.

## Measurement

Staging, 2026-11-05, read from the Backup objects and the sidecar log:

- **The WAL position advanced by exactly 4,640 MiB a day.** It was the same
  every day from 2026-10-29 to 2026-11-05. That is 290 segments of 16 MiB, one
  every 5 minutes.
- **The sidecar archived 291 WAL files in 24 hours.**
- **Base backups are small.** Each finished in 20–30 s.

CloudNativePG sets `archive_timeout` to 5 minutes. Postgres then closes the
current WAL segment at least that often, even when little was written. A
segment closed early is still a full 16 MiB file. The archive therefore grew
with the clock, not with the data. gzip shrinks each file to about 0.5 MB:
1 GB over 7 days is ~145 MB a day, divided by 290 files.

At that rate, prod's 30-day retention would hold about 4 GB before any real
dataset arrives.

**Not measured:** how much of the 0.5 MB is real change and how much is
padding that gzip does not remove. The bucket's daily growth after this
change answers it (see Risks).

## Requirements and constraints

- Stay inside R2's free tier while the datasets grow.
- A restore must still reach a recent point. What a lost hour costs:
  - Bronze data can be re-loaded from USGS, up to 30 days back.
  - Every pipeline run is idempotent.
  - Dagster's run history and the DuckLake catalog cannot be re-loaded. Losing
    an hour of either is tolerable; losing either one entirely is not.
- No new component and no new memory.

## Alternatives considered

- **Base backups 3 days a week.** It saves little space, because base backups
  are not what fills the bucket. Restores would get slower: they would replay
  up to two days of WAL.
- **Shorter retention only.** It cuts space in proportion, but most of what
  stays is still clock-driven padding.
- **Stop WAL archiving and keep daily base backups.** The RPO would become up
  to 24 hours. That gives up point-in-time recovery to save what
  `archive_timeout` already saves.
- **Leave it and pay past 10 GB.** R2 bills cents per GB over the free tier.
  The cost is small, but the platform's goal is to run on free tiers where it
  can, and the growth was waste, not data.

## Decision

1. **`archive_timeout: 1h`** in both Clusters. Postgres still archives a
   segment as soon as it fills. The hour is only the longest a partly filled
   segment waits. The parameter is applied by reload, without a restart.
2. **Prod retention from 30 to 14 days.** Two weeks leaves room to notice a
   broken backup twice over and still restore from a good one. Staging stays
   at 7 days.
3. The daily base backup at 03:00 UTC is unchanged.

## Consequences

**Positive.**

- **About 24 forced segment closes a day instead of 290.** If padding
  dominates, the archive should shrink by up to 12 times. The bucket
  measurement confirms or corrects this.
- Prod keeps half as many days.

**Negative.**

- **RPO from about 5 minutes to about 1 hour.** RPO is the amount of writes
  that can be lost with the node: the writes in a segment that is not yet
  closed exist only on the node's disk. Pipeline data is rebuilt by a
  re-load. Dagster's history of that hour is not.
- **Prod's point-in-time recovery reaches 14 days back, not 30.**

## Risks, cost, and reversal

- **Risk: the saving is smaller than expected**, because most of each file is
  real change. Examples are Dagster's daemon heartbeats in staging and the
  full-page writes after each checkpoint. Check: the operator reads each
  backup bucket's size in the R2 console on 2026-11-05 and again two days
  later. If growth is still above ~50 MB a day per bucket, the next levers
  are `wal_compression` and a longer `checkpoint_timeout`, each with its own
  measurement.
- **Risk: an hour of history is lost when the node dies.** Accepted for now.
  It is revisited when a dataset that cannot be re-loaded enters, the M6
  trigger in the roadmap.
- **Cost:** none. No new component.
- **Reversal:** remove `archive_timeout` from both `cluster.yaml` files, and
  set prod's `retentionPolicy` back to `30d`. Backups already deleted by the
  shorter retention do not come back.
