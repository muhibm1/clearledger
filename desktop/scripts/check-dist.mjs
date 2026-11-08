// A dry check of the `npm run dist` configuration without building a dmg: electron-builder's own schema check on the
// merged config, plus the files the build copies (main entry, renderer, recordings, examples) must exist.
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import * as path from "node:path";

const require = createRequire(import.meta.url);
const { getConfig, validateConfiguration } = require("app-builder-lib/out/util/config/config");
const { DebugLogger } = require("builder-util");

const here = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");
const pkg = JSON.parse(readFileSync(path.join(here, "package.json"), "utf8"));
const problems = [];
const config = await getConfig(here, null, null);
try {
  await validateConfiguration(config, new DebugLogger(false));
} catch (e) {
  problems.push(`electron-builder rejected the config: ${e.message}`);
}
const need = (p, what) => { if (!existsSync(path.join(here, p))) problems.push(`${what} is missing: ${p} (run npm run build first)`); };
need(pkg.main, "the main entry");
need("build/preload.js", "the preload script");
need("build/renderer/index.html", "the page");
need("build/renderer/app.js", "the page script");
for (const r of config.extraResources ?? []) need(r.from, `extra resource ${r.to}`);
for (const f of ["recorded/demo.txt", "recorded/walkthrough.txt", "recorded/walkthrough.json", "recorded/manifest.json", "../docs/examples/trace-northwind.txt", "../docs/examples/preflight-drop-column.txt"]) need(f, "a recording the app shows");
const mac = config.mac ?? {};
const targets = (mac.target ?? []).map((t) => (typeof t === "string" ? t : t.target));
if (!targets.includes("dmg")) problems.push("mac.target does not include dmg");
if (!/--mac dmg --publish never/.test(pkg.scripts.dist)) problems.push("the dist script does not build a dmg without publishing");
console.log(JSON.stringify({ appId: config.appId, productName: config.productName, mac: { targets, arch: (mac.target ?? []).flatMap((t) => t.arch ?? []), identity: mac.identity }, extraResources: config.extraResources, problems }, null, 2));
process.exit(problems.length ? 1 : 0);
