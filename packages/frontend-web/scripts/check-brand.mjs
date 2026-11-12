// Fails when the brand string appears in src/ or messages/ outside the single config file.
// Components must read BRAND_NAME (src/config/brand.ts); catalogs use the {brand} placeholder.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join, relative, sep } from 'node:path';

export const DEFAULT_BRAND = 'mashqgoh';
const ALLOWED = new Set(['src/config/brand.ts']);
const EXT = /\.(ts|tsx|js|jsx|mjs|css|json|md|svg|html)$/;

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* walk(p);
    else if (EXT.test(name)) yield p;
  }
}

/** @returns {{file: string, line: number, text: string}[]} */
export function findBrandViolations(root, dirs = ['src', 'messages'], brands = [DEFAULT_BRAND]) {
  const wanted = [...new Set([...brands, process.env.NEXT_PUBLIC_BRAND_NAME].filter(Boolean))].map((b) =>
    b.toLowerCase(),
  );
  const out = [];
  for (const d of dirs) {
    let files;
    try {
      files = [...walk(join(root, d))];
    } catch {
      continue;
    }
    for (const file of files) {
      const rel = relative(root, file).split(sep).join('/');
      if (ALLOWED.has(rel)) continue;
      readFileSync(file, 'utf8')
        .split('\n')
        .forEach((text, i) => {
          if (wanted.some((b) => text.toLowerCase().includes(b))) {
            out.push({ file: rel, line: i + 1, text: text.trim() });
          }
        });
    }
  }
  return out;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const bad = findBrandViolations(join(fileURLToPath(import.meta.url), '..', '..'));
  for (const v of bad) console.error(`${v.file}:${v.line}: brand string outside src/config/brand.ts: ${v.text}`);
  process.exit(bad.length ? 1 : 0);
}
