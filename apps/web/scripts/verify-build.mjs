// Post-build guard for the Vercel failure
//   ENOENT: lstat .../.next/server/app/(authenticated)/page_client-reference-manifest.js
// Next.js registers every page it routes in app-paths-manifest.json, and Vercel's file tracing
// then expects a `<page>_client-reference-manifest.js` next to each compiled page. Two pages that
// resolve to the same URL (for example app/page.tsx and app/(group)/page.tsx) leave one entry
// registered without that file. Fail here, locally and in CI, instead of during the deployment.
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const root = new URL("..", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");
const serverDir = join(root, ".next", "server");
const manifestPath = join(serverDir, "app-paths-manifest.json");

if (!existsSync(manifestPath)) {
  console.error(`verify-build: ${manifestPath} not found. Run \`next build\` first.`);
  process.exit(1);
}

const entries = Object.entries(JSON.parse(readFileSync(manifestPath, "utf8")));
const problems = [];

// 1. Every routed page must have its client reference manifest.
for (const [route, file] of entries) {
  if (!route.endsWith("/page")) continue;
  const manifest = join(serverDir, file.replace(/\.js$/, "_client-reference-manifest.js"));
  if (!existsSync(manifest)) problems.push(`missing client-reference manifest for ${route}: ${relative(root, manifest)}`);
}

// 2. No two pages may resolve to the same URL.
const routesPath = join(root, ".next", "app-path-routes-manifest.json");
if (existsSync(routesPath)) {
  const byUrl = new Map();
  for (const [route, url] of Object.entries(JSON.parse(readFileSync(routesPath, "utf8")))) {
    if (!route.endsWith("/page")) continue;
    byUrl.set(url, [...(byUrl.get(url) ?? []), route]);
  }
  for (const [url, routes] of byUrl) {
    if (routes.length > 1) problems.push(`duplicate pages resolve to ${url}: ${routes.join(", ")}`);
  }
}

// 3. Every page source on disk must be compiled (guards against pages silently dropped).
const appDir = join(root, "app");
const sources = [];
(function walk(dir) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) walk(full);
    else if (/^page\.(tsx|ts|jsx|js)$/.test(name)) sources.push(full);
  }
})(appDir);
const compiled = new Set(entries.map(([route]) => route));
for (const source of sources) {
  const route = "/" + relative(appDir, source).replace(/\\/g, "/").replace(/\.(tsx|ts|jsx|js)$/, "");
  if (!compiled.has(route)) problems.push(`page source not compiled: ${relative(root, source)} (${route})`);
}

if (problems.length) {
  console.error("verify-build FAILED:\n - " + problems.join("\n - "));
  process.exit(1);
}
console.log(`verify-build: ${entries.filter(([r]) => r.endsWith("/page")).length} pages OK (manifests present, no duplicate routes).`);