# Site

Static pages for ofantini.com. Astro, exact version pinned in `package.json`
(`7.4.7` as of this writing), output is static files only. Nothing here is a
server, and the build does not call an adapter.

Two pages:

- **Home** (`/`) — what the platform is: cheap to run, small enough to explain,
  and it grows by adding a pipeline. The measured figures are the node's price,
  shape, memory, the CI request ceiling, and the rehearsed rebuild.
- **USGS earthquakes** (`/showcase/usgs`) — the problem first (a small public
  feed), then the path the scheduled job runs (dlt, then DuckLake on Cloudflare
  R2, then dbt, then Dagster), then the rows, time and memory recorded in
  `docs/architecture.md` and `docs/roadmap.md`. A later section keeps the
  earlier Postgres CronJob measurements separate. The page says the warehouse
  decision (ADR-0023) is still proposed, and that the architecture's storage
  section still keeps every layer in Postgres until that spike passes.

Every number on those pages is an entry in `src/figures.json`, with the
section it was copied from. `scripts/check-dist.mjs` fails the build if a built
page shows a number that file does not contain, if a listed value is missing,
or if the built files reference anything other than this site, the two source
documents on GitHub, and the SVG namespace.

## Run

Node.js `>=22.23.4`, which is the version Astro 7.4.7 requires.

```bash
cd products/site
npm ci
npm run build
npm run preview
```

`npm run build` turns Astro's CLI telemetry off, runs `astro build`, then
`scripts/check-dist.mjs`. `npm run dev` is the local preview while editing.
The acceptance check is `npm ci && npm run build`, from this directory.

Open the built files under `dist/`, or the preview, at `/` and
`/showcase/usgs`.

## What is not in the build

- The exact monthly price of the CAX21. The architecture says it is roughly
  double the CAX11's EUR 7.37, and that the exact price is the one the Console
  shows.
- How many files the lake holds, and how many bytes. Neither source records
  them, so the page says they are not recorded.
- Evidence dashboards, and a deploy to Cloudflare Pages. Both stay open on the
  roadmap. This repository's CI does not build the site; adding that check
  would touch `.github/`, which this change does not.
- No Pod is added. The architecture lists the site's RAM on the node as 0.
  The resource-budget check has nothing new to count.

## Motion, type, and what the browser loads

Motion is CSS: transitions on hover, a scroll-linked entrance
(`animation-timeline: view()`, with a short timed fade where that is
unsupported), and Astro's view transitions (`ClientRouter`) between the two
pages. All of it sits inside `prefers-reduced-motion: no-preference`. The
reduce query also collapses view-transition animations.

Fraunces and IBM Plex Mono are the OFL faces shipped by the fontsource
packages. The build copies them into `dist/`. The pages do not request a font
host, an analytics script, or a cookie.

To look at it: build, preview, and resize to a phone width (no horizontal
scroll) and a desktop width. In the browser's rendering tools, set
`prefers-reduced-motion: reduce` and reload: the entrance animation and the
hover transition should be absent, and the text should already be visible.
