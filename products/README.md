# products/

One directory per thing a consumer sees. Products change often and affect one
audience each.

| Product | Form | State |
|---|---|---|
| [`site/`](site/README.md) | Astro, static pages | M4. Source is in the repo: home, and the USGS showcase. Not published to Cloudflare Pages yet |
| dashboards | Evidence, built after each run | M4, not started ([ADR-0022](../docs/adr/0022-platform-as-a-portfolio-of-architectures.md)) |

The globe left the milestones
([ADR-0022](../docs/adr/0022-platform-as-a-portfolio-of-architectures.md)). It is
not a product in this directory.

The site does not query the cluster: it is files built ahead of time. A product
that serves data reads published output, not a service's internals
([ADR-0008](../docs/adr/0008-postgres-for-all-layers.md),
[ADR-0022](../docs/adr/0022-platform-as-a-portfolio-of-architectures.md)).

This directory is deliberately agnostic about form: a report, an app, a notebook,
or a BI project are all products. Whatever lands here must be reviewable as
text — see [ADR-0006](../docs/adr/0006-monorepo-layout.md) and
[`../.gitattributes`](../.gitattributes).
