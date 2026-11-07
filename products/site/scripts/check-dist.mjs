import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const dist = join(root, "dist");
const inventory = JSON.parse(readFileSync(join(root, "src/figures.json"), "utf8"));

const pages = {
  home: "index.html",
  usgs: "showcase/usgs/index.html",
};
const otherHtml = new Set(["404.html"]);

const allowedUrl = [
  /^https:\/\/ofantini\.com\/?$/,
  /^https:\/\/ofantini\.com\/showcase\/usgs\/?$/,
  /^https:\/\/github\.com\/FilipeFantini\/lagom(?:\/blob\/main\/docs\/(?:architecture|roadmap)\.md)?$/,
  /^http:\/\/www\.w3\.org\/2000\/svg$/,
];

const banned = [
  "googletagmanager",
  "google-analytics",
  "fonts.googleapis",
  "fonts.gstatic",
  "cloudflareinsights",
  "plausible",
  "umami",
  "cdn.jsdelivr",
  "unpkg.com",
  "document.cookie",
  "localStorage",
  "sessionStorage",
];

const failures = [];

function fail(message) {
  failures.push(message);
}

function walk(dir) {
  const found = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) found.push(...walk(path));
    else found.push(path);
  }
  return found;
}

function decode(value) {
  return value
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&#39;/g, "'")
    .replace(/&apos;/g, "'")
    .replace(/&quot;/g, '"');
}

function bodyText(html) {
  const body = html.split(/<body\b[^>]*>/i)[1]?.split("</body>")[0] ?? "";
  const stripped = body
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, " ")
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ");
  return decode(stripped);
}

function allowlist(items) {
  return items.map((item) => [item.value, item.label, item.detail, item.source].join("\n")).join("\n");
}

const files = walk(dist);
const htmlFiles = files.filter((path) => path.endsWith(".html"));
const expected = new Set([...Object.values(pages), ...otherHtml].map((rel) => join(dist, rel)));

for (const path of htmlFiles) {
  if (!expected.has(path)) fail(`unexpected html file ${path}`);
}

for (const [page, rel] of Object.entries(pages)) {
  const path = join(dist, rel);
  const html = readFileSync(path, "utf8");
  const items = inventory.figures.filter((figure) => figure.page === page);
  const allowed = allowlist(items);
  const text = bodyText(html);
  for (const token of text.match(/\d[\d,.]*/g) ?? []) {
    if (!allowed.includes(token)) fail(`${rel} shows ${token}, which is not in the figure inventory`);
  }
  for (const item of items) {
    const marker = `data-figure="${item.id}"`;
    const start = html.indexOf(marker);
    if (start < 0) {
      fail(`${rel} is missing figure ${item.id}`);
      continue;
    }
    const slice = html.slice(start, html.indexOf("</article>", start));
    const value = slice.match(/<p class="figure-value">([\s\S]*?)<\/p>/);
    if (!value || decode(value[1]).trim() !== item.value) {
      fail(`${rel} figure ${item.id} value is not ${item.value}`);
    }
    if (!decode(slice).includes(item.detail)) fail(`${rel} figure ${item.id} is missing its detail`);
  }
}

const missing = readFileSync(join(dist, "404.html"), "utf8");
for (const token of bodyText(missing).match(/\d[\d,.]*/g) ?? []) {
  fail(`404.html shows a number (${token})`);
}

const css = files.filter((path) => path.endsWith(".css")).map((path) => readFileSync(path, "utf8")).join("\n");
if (!css.includes("prefers-reduced-motion")) fail("built CSS has no prefers-reduced-motion rule");
if (!css.includes("animation-timeline")) fail("built CSS has no scroll-linked animation");
if (!css.includes("view-transition")) fail("built CSS has no view-transition rule");

for (const path of files) {
  if (path.endsWith(".woff") || path.endsWith(".woff2")) continue;
  const text = readFileSync(path, "utf8");
  const lower = text.toLowerCase();
  for (const needle of banned) {
    if (lower.includes(needle)) fail(`${path} contains ${needle}`);
  }
  for (const match of text.match(/\bhttps?:\/\/[^\s"'<>)]+/g) ?? []) {
    const url = match.replace(/[.,;]+$/, "");
    if (!allowedUrl.some((pattern) => pattern.test(url))) fail(`${path} references ${url}`);
  }
  if (path.endsWith(".html")) {
    for (const tag of text.matchAll(/<script\b[^>]*>/gi)) {
      const src = tag[0].match(/\bsrc="([^"]*)"/);
      if (src && !src[1].startsWith("/")) fail(`${path} loads a script from ${src[1]}`);
    }
    for (const tag of text.matchAll(/<link\b[^>]*>/gi)) {
      const rel = tag[0].match(/\brel="([^"]*)"/);
      const href = tag[0].match(/\bhref="([^"]*)"/);
      if (!href) continue;
      const relation = rel?.[1] ?? "";
      if (relation === "canonical") continue;
      if (!href[1].startsWith("/") && !href[1].startsWith("#")) {
        fail(`${path} loads a link from ${href[1]}`);
      }
    }
  }
}

if (failures.length > 0) {
  for (const message of failures) console.error(message);
  process.exit(1);
}
