import { copyFileSync, mkdirSync } from "node:fs";
mkdirSync("build/renderer", { recursive: true });
for (const f of ["index.html", "style.css"]) copyFileSync(`src/renderer/${f}`, `build/renderer/${f}`);
