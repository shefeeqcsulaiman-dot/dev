// Regenerates public/taxflow/src/app.min.js from app.js.
// Run this after editing app.js: npm run build:min
//
// The output's first line embeds a SHA-256 of the exact app.js content it was
// built from. The backend (see app/main.py) only serves this minified file
// when that hash still matches the current app.js — if you forget to run
// this after an edit, the server transparently falls back to serving the
// original app.js instead of silently shipping stale minified code.
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import * as esbuild from "esbuild";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const srcPath = path.join(__dirname, "..", "public", "taxflow", "src", "app.js");
const outPath = path.join(__dirname, "..", "public", "taxflow", "src", "app.min.js");

const source = readFileSync(srcPath);
const hash = createHash("sha256").update(source).digest("hex");

const result = esbuild.transformSync(source.toString("utf-8"), { minify: true, loader: "js" });

writeFileSync(outPath, `//SOURCE_SHA256:${hash}\n${result.code}`);
console.log(`Built ${outPath}`);
console.log(`  source: ${source.length} bytes, sha256=${hash}`);
console.log(`  minified: ${Buffer.byteLength(result.code)} bytes`);
