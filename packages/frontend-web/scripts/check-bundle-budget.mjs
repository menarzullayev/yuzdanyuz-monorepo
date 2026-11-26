// Performance budget (audit I03): gzip size of the JavaScript each page loads first.
//
// First load of a route = the runtime chunks every page gets (build-manifest.json
// rootMainFiles) + that route's own entry chunks (page_client-reference-manifest.js).
// Run after `next build`; fails when a route or the whole client bundle is over budget.
//   node scripts/check-bundle-budget.mjs            (uses bundle-budget.json)
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';

export function walk(dir, test) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p, test));
    else if (test(p)) out.push(p);
  }
  return out;
}

/** Route name -> chunk paths relative to .next, from one client-reference manifest file. */
export function routeEntries(manifestSource) {
  const routes = {};
  const re = /__RSC_MANIFEST\["([^"]+)"\]\s*=\s*(\{.*\});?\s*$/gm;
  let m;
  while ((m = re.exec(manifestSource))) {
    const data = JSON.parse(m[2]);
    const files = new Set();
    for (const list of Object.values(data.entryJSFiles ?? {})) for (const f of list) files.add(f);
    routes[m[1]] = [...files];
  }
  return routes;
}

export function measure(nextDir) {
  const gz = new Map();
  const size = (rel) => {
    if (!gz.has(rel)) gz.set(rel, gzipSync(readFileSync(join(nextDir, rel))).length);
    return gz.get(rel);
  };
  const build = JSON.parse(readFileSync(join(nextDir, 'build-manifest.json'), 'utf8'));
  const root = build.rootMainFiles ?? [];
  const routes = {};
  const manifests = walk(join(nextDir, 'server', 'app'), (p) => p.endsWith('_client-reference-manifest.js'));
  for (const file of manifests) {
    for (const [route, files] of Object.entries(routeEntries(readFileSync(file, 'utf8')))) {
      const all = new Set([...root, ...files]);
      routes[route] = [...all].reduce((n, f) => n + size(f), 0);
    }
  }
  const chunks = walk(join(nextDir, 'static'), (p) => p.endsWith('.js'));
  const total = chunks.reduce((n, p) => n + size(relative(nextDir, p).split(sep).join('/')), 0);
  return { routes, total };
}

export function check({ routes, total }, budget) {
  const problems = [];
  for (const [route, bytes] of Object.entries(routes)) {
    if (bytes > budget.routeFirstLoadKb * 1024)
      problems.push(`${route}: ${(bytes / 1024).toFixed(1)} KB > ${budget.routeFirstLoadKb} KB`);
  }
  if (total > budget.totalKb * 1024)
    problems.push(`all client JS: ${(total / 1024).toFixed(1)} KB > ${budget.totalKb} KB`);
  return problems;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const here = fileURLToPath(new URL('..', import.meta.url));
  const budget = JSON.parse(readFileSync(join(here, 'bundle-budget.json'), 'utf8'));
  const result = measure(join(here, '.next'));
  const worst = Object.entries(result.routes).sort((a, b) => b[1] - a[1]).slice(0, 5);
  for (const [r, b] of worst) console.log(`${(b / 1024).toFixed(1).padStart(7)} KB  ${r}`);
  console.log(`${(result.total / 1024).toFixed(1).padStart(7)} KB  all client JS (gzip)`);
  const problems = check(result, budget);
  if (Object.keys(result.routes).length === 0) problems.push('no routes found: run `next build` first');
  if (problems.length) {
    console.error('Over budget:\n  ' + problems.join('\n  '));
    process.exit(1);
  }
  console.log(`ok: within budget (route ${budget.routeFirstLoadKb} KB, total ${budget.totalKb} KB)`);
}
