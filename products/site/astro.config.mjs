import { defineConfig } from "astro/config";

// Static files only. No adapter, no server, no request at runtime
// beyond the files this build emits.
export default defineConfig({
  site: "https://ofantini.com",
  output: "static",
  trailingSlash: "ignore",
  build: {
    format: "directory",
  },
});
