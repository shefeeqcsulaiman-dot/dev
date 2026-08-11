// Rewrites the `?v=...` cache-busting query strings on src/app.js and
// src/styles.css in index.html and hrms.html, deriving each value from that
// asset's own content hash instead of a hand-maintained date-ish string.
//
// Previously `?v=` was edited by hand in two files and had to be kept in
// sync manually (see the comment at the top of index.html/hrms.html) — easy
// to forget, and since these assets are served with a year-long immutable
// Cache-Control once their `?v=` URL has been fetched once, forgetting to
// bump it means already-visited browsers keep serving stale JS/CSS
// indefinitely even after the server has the new file. Run this after
// editing app.js or styles.css: npm run inject-versions (also wired into
// the Docker build stage so it happens automatically on every deploy).
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const taxflowDir = path.join(__dirname, "..", "public", "taxflow");

function shortHash(filePath) {
  const content = readFileSync(filePath);
  return createHash("sha256").update(content).digest("hex").slice(0, 10);
}

const jsVersion = shortHash(path.join(taxflowDir, "src", "app.js"));
const cssVersion = shortHash(path.join(taxflowDir, "src", "styles.css"));

const htmlFiles = ["index.html", "hrms.html"].map((name) => path.join(taxflowDir, name));

for (const htmlPath of htmlFiles) {
  let html = readFileSync(htmlPath, "utf-8");
  html = html.replace(/src\/app\.js\?v=[^"']+/g, `src/app.js?v=${jsVersion}`);
  html = html.replace(/src\/styles\.css\?v=[^"']+/g, `src/styles.css?v=${cssVersion}`);
  writeFileSync(htmlPath, html);
  console.log(`Updated ${htmlPath}`);
}

console.log(`  app.js version:     ${jsVersion}`);
console.log(`  styles.css version: ${cssVersion}`);
