{% docs __overview__ %}
# Lagom

Lagom is an open-source batch data platform that runs real analytics on a
single small server. Jobs ingest public data, build and test the warehouse
models described here, and publish static reports.

- [ofantini.com](https://ofantini.com) presents Lagom and the infrastructure
  behind it.
- [The repository](https://github.com/FilipeFantini/lagom) holds these models,
  their tests and the decisions behind them.
- The reports are listed under **Exposures** in the Project tab, each beside
  the models it reads.

## Reading this page

Sources are the raw data as loaded. Silver models clean it and keep one row per
record. Gold models are what the reports read: each has a description, an
enforced contract and an owner. The lineage graph, opened by the button at the
bottom right, shows where each table comes from.

The pipeline rebuilds this page after each successful run, from the same code
and the lake's current tables.
{% enddocs %}
