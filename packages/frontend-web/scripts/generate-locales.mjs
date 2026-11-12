// Regenerates messages/uz-Cyrl.json and messages/uz-yangi.json from messages/uz.json.
// Run: npm run i18n:generate   (CI/test fails if the committed files drift from uz.json).
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { mapCatalog, toCyrillic, toNewAlphabet } from './translit.mjs';

const dir = join(dirname(fileURLToPath(import.meta.url)), '..', 'messages');
const uz = JSON.parse(readFileSync(join(dir, 'uz.json'), 'utf8'));

export const generated = {
  'uz-Cyrl': mapCatalog(uz, toCyrillic),
  'uz-yangi': mapCatalog(uz, toNewAlphabet),
};

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  for (const [name, catalog] of Object.entries(generated)) {
    writeFileSync(join(dir, `${name}.json`), JSON.stringify(catalog, null, 2) + '\n');
    console.log(`wrote messages/${name}.json`);
  }
}
