import { describe, expect, it } from 'vitest';
import { BRAND_NAME } from '../src/config/brand';
import { checkPassword, passwordValid, strength } from '../src/lib/password';

const failed = (pw: string, ctx = {}) =>
  checkPassword(pw, ctx)
    .filter((h) => !h.ok)
    .map((h) => h.id);

describe('password hints (mirror of the server policy)', () => {
  it('accepts a good password', () => {
    expect(passwordValid('Tulpor-7-quyosh!', { username: 'ali.valiyev', firstName: 'Ali' })).toBe(true);
  });
  it('length 8..128', () => {
    expect(failed('Ab1!xyz')).toContain('length');
    expect(failed('a1'.repeat(65))).toContain('length');
  });
  it('72 UTF-8 bytes max', () => {
    expect(failed('ё'.repeat(40) + 'x1')).toContain('bytes');
  });
  it('not numeric only, not one repeated char', () => {
    expect(failed('48151623')).toContain('notNumeric');
    expect(failed('aaaaaaaa')).toContain('notRepeated');
  });
  it('platform words and 123456 variants', () => {
    expect(failed(`my${BRAND_NAME}pass9`)).toContain('noLocalWords');
    expect(failed('Milliy-Test-77')).toContain('noLocalWords');
    expect(failed('xq123456zz')).toContain('noLocalWords');
  });
  it('equal or similar to username/name/email', () => {
    expect(failed('ali.valiyev', { username: 'Ali.Valiyev' })).toContain('notSimilar');
    expect(failed('valiyev2026!', { lastName: 'Valiyev' })).toContain('notSimilar');
    expect(failed('Zx9!kLmq', { email: 'zebo.k@x.uz', username: 'zebo' })).toEqual([]);
  });
  it('strength is weak whenever a rule fails', () => {
    expect(strength('abc')).toBe('weak');
    expect(strength('Tulpor-7-quyosh!')).toBe('strong');
  });
});
