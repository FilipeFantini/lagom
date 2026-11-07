import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const dist = join(root, "dist");
const figures = JSON.parse(readFileSync(join(root, "src/figures.json"), "utf8")).figures;
const biography = JSON.parse(readFileSync(join(root, "src/biography.json"), "utf8")).facts;

const allowedUrl = [
  /^https:\/\/ofantini\.com\/?$/,
  /^https:\/\/ofantini\.com\/projects\/?$/,
  /^https:\/\/ofantini\.com\/projects\/lagom\/?$/,
  /^https:\/\/ofantini\.com\/projects\/lagom\/(?:infrastructure|decisions|ingestion|modelling|visualization|synthesis|usgs)\/?$/,
  /^https:\/\/github\.com\/FilipeFantini\/lagom(?:\/blob\/main\/docs\/(?:architecture|roadmap|adr\/[0-9a-z-]+)\.md)?$/,
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
  return decode(
    body
      .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, " ")
      .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, " ")
      .replace(/<[^>]+>/g, " "),
  );
}

function allowlist(items, fields) {
  return items.map((item) => fields.map((field) => item[field] ?? "").join("\n")).join("\n");
}

const files = walk(dist);
const htmlFiles = files.filter((path) => path.endsWith(".html"));

for (const path of htmlFiles) {
  const html = readFileSync(path, "utf8");
  const rel = path.slice(dist.length + 1);
  const inventory = html.match(/data-inventory="([^"]+)"/)?.[1];
  const figurePage = html.match(/data-figure-page="([^"]*)"/)?.[1] ?? "";
  const text = bodyText(html);
  const tokens = text.match(/\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?/g) ?? [];

  if (inventory !== "biography" && inventory !== "platform" && inventory !== "none") {
    fail(`${rel} has no inventory marker`);
    continue;
  }

  if (inventory === "none") {
    for (const token of tokens) fail(`${rel} shows ${token}, and this page declares no numbers`);
  }

  if (inventory === "biography") {
    const allowed = allowlist(biography, ["id", "value"]);
    for (const token of tokens) {
      if (!allowed.includes(token)) fail(`${rel} shows ${token}, which is not in the biography`);
    }
    for (const fact of biography) {
      if (!text.includes(fact.value)) fail(`${rel} is missing biography fact ${fact.id}`);
    }
  }

  if (inventory === "platform") {
    const items = figures.filter((figure) => figure.page === figurePage);
    if (items.length === 0) fail(`${rel} names figure page ${figurePage}, which has no figures`);
    const allowed = allowlist(items, ["value", "label", "detail", "source"]);
    for (const token of tokens) {
      if (!allowed.includes(token)) fail(`${rel} shows ${token}, which is not in the platform figures for ${figurePage}`);
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
    }
  }
}

const css = files.filter((path) => path.endsWith(".css")).map((path) => readFileSync(path, "utf8")).join("\n");
if (!css.includes("prefers-reduced-motion")) fail("built CSS has no prefers-reduced-motion rule");
if (!css.includes("perspective")) fail("built CSS has no 3D perspective");
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
  if (!path.endsWith(".html")) continue;
  for (const tag of text.matchAll(/<script\b[^>]*>/gi)) {
    const src = tag[0].match(/\bsrc="([^"]*)"/);
    if (src && !src[1].startsWith("/")) fail(`${path} loads a script from ${src[1]}`);
  }
  for (const tag of text.matchAll(/<link\b[^>]*>/gi)) {
    const rel = tag[0].match(/\brel="([^"]*)"/)?.[1] ?? "";
    const href = tag[0].match(/\bhref="([^"]*)"/)?.[1];
    if (!href || rel === "canonical") continue;
    if (!href.startsWith("/") && !href.startsWith("#")) fail(`${path} loads a link from ${href}`);
  }
}

if (failures.length > 0) {
  for (const message of failures) console.error(message);
  process.exit(1);
}
