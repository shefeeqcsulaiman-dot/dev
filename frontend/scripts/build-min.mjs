// Regenerates public/taxflow/src/{app,ess}.min.js from their .js sources.
// Run this after editing app.js or ess.js: npm run build:min
//
// Each output's first line embeds a SHA-256 of the exact source it was built
// from. The backend (see app/main.py's _resolve_minified_js()) only serves a
// minified file when that hash still matches its current source — if you
// forget to run this after an edit, the server transparently falls back to
// serving the original source instead of silently shipping stale minified code.
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import * as esbuild from "esbuild";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const srcDir = path.join(__dirname, "..", "public", "taxflow", "src");

function buildOne(name) {
  const srcPath = path.join(srcDir, `${name}.js`);
  const outPath = path.join(srcDir, `${name}.min.js`);

  const source = readFileSync(srcPath);
  const hash = createHash("sha256").update(source).digest("hex");

  const result = esbuild.transformSync(source.toString("utf-8"), { minify: true, loader: "js" });

  writeFileSync(outPath, `//SOURCE_SHA256:${hash}\n${result.code}`);
  console.log(`Built ${outPath}`);
  console.log(`  source: ${source.length} bytes, sha256=${hash}`);
  console.log(`  minified: ${Buffer.byteLength(result.code)} bytes`);
}

for (const name of ["app", "ess"]) {
  buildOne(name);
}
