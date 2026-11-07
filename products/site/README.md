# Site

Filipe Fantini's personal site for ofantini.com. Astro, exact version pinned in
`package.json`, static output only.

Lagom is one project on the site, a personal laboratory. It is not an
employer's system, and it is not the whole of the site.

## Pages

| Path | What it claims |
|---|---|
| `/` | Biography: roles, dates and the figures copied from the supplied CV. No contact details, and the CV image is not published. |
| `/projects` | One project, Lagom. No second project is invented. |
| `/projects/lagom` | How the laboratory fits together, including the open warehouse decision. No measurements on this page. |
| `/projects/lagom/infrastructure` | One node, the rejected alternative, the recorded memory and cost. |
| `/projects/lagom/decisions` | ADRs, including ADR-0023 still proposed. |
| `/projects/lagom/ingestion` | dlt for the batch feed. The event tools are chosen and not running. |
| `/projects/lagom/modelling` | Bronze, silver, gold, tests, and the freshness check that is not asserted yet. |
| `/projects/lagom/visualization` | Charts are planned. None are published. |
| `/projects/lagom/synthesis` | What the rebuild showed, and what is still open. |
| `/projects/lagom/usgs` | The earthquake showcase: problem, the path the scheduled job runs, then the measured rows, time and memory. |

## Two inventories

Platform measurements live in `src/figures.json`. Every one is copied from
`docs/architecture.md` or `docs/roadmap.md`. Professional facts live in
`src/biography.json`, copied from the CV, and are not mixed into the platform
file.

`scripts/check-dist.mjs` reads `data-inventory` on each built page.

- `biography` — every CV fact's value is present, and every number in the body is in that file.
- `platform` — `data-figure-page` selects the figures, each listed value is present, and every number in the body is in those figures.
- `none` — the body has no number. Used for Projects, the Lagom overview and the missing page.

A trailing comma or period is not part of the number. Thousands separators and decimal points are.

The check also fails a resource loaded from anywhere other than this site, the architecture, the roadmap, an ADR on GitHub, or the SVG namespace. It does not decide whether a sentence is true. That is review.

## Run

Node.js `>=22.23.4`.

```bash
cd products/site
npm ci
npm run build
npm run preview
```

`npm run build` turns Astro's CLI telemetry off, runs `astro build`, then the check above.

Home, on a wide screen with motion allowed, is a bench drawn with CSS perspective. The three pieces are ordinary links. On a phone, and whenever `prefers-reduced-motion: reduce` is set, the same links are a flat stack. No drag, no scroll hijacking. Projects is the same kind of link, once.

## Not in this build

Cloudflare Pages, the Evidence gallery, and a CI job for the site. The last would edit `.github/`. No Pod is added. The architecture lists the site's RAM on the node as 0.
