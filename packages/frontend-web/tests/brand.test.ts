import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { findBrandViolations } from '../scripts/check-brand.mjs';

const root = join(__dirname, '..');

describe('brand string', () => {
  it('appears in src/ and messages/ only inside src/config/brand.ts', () => {
    expect(findBrandViolations(root)).toEqual([]);
  });

  it('NEGATIVE: the check catches a hard-coded brand in a component and in a catalog', () => {
    const tmp = mkdtempSync(join(tmpdir(), 'brand-'));
    mkdirSync(join(tmp, 'src', 'config'), { recursive: true });
    mkdirSync(join(tmp, 'messages'));
    writeFileSync(join(tmp, 'src', 'config', 'brand.ts'), "export const B = 'mashqgoh';\n");
    writeFileSync(join(tmp, 'src', 'Header.tsx'), '<h1>MashqGoh</h1>\n');
    writeFileSync(join(tmp, 'messages', 'uz.json'), '{"a": "Mashqgoh ga xush kelibsiz"}\n');
    const bad = findBrandViolations(tmp);
    expect(bad.map((v) => v.file).sort()).toEqual(['messages/uz.json', 'src/Header.tsx']);
  });
});
