import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const env = { ...process.env, ASTRO_TELEMETRY_DISABLED: "1" };
const astro = fileURLToPath(new URL("../node_modules/astro/bin/astro.mjs", import.meta.url));

const build = spawnSync(process.execPath, [astro, "build"], {
  cwd: root,
  env,
  stdio: "inherit",
});

if (build.error) {
  console.error(build.error);
  process.exit(1);
}
if (build.status !== 0) {
  process.exit(build.status ?? 1);
}

const check = spawnSync(process.execPath, [fileURLToPath(new URL("./check-dist.mjs", import.meta.url))], {
  cwd: root,
  stdio: "inherit",
});

if (check.error) {
  console.error(check.error);
  process.exit(1);
}
process.exit(check.status ?? 1);
