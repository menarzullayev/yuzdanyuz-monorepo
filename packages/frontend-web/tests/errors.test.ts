import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { ApiError, errorText, fieldTexts, KNOWN_ERROR_CODES, parseError } from '../src/lib/api/errors';

const catalog = (l: string) => JSON.parse(readFileSync(join(__dirname, '..', 'messages', `${l}.json`), 'utf8'));

function translator(l: string) {
  const c = catalog(l);
  const get = (key: string) => key.split('.').reduce<unknown>((o, k) => (o as Record<string, unknown> | undefined)?.[k], c);
  return {
    has: (k: string) => typeof get(k) === 'string',
    t: (k: string, v?: Record<string, string | number>) =>
      String(get(k) ?? `MISSING:${k}`).replace(/\{(\w+)\}/g, (_, n) => String(v?.[n] ?? '')),
  };
}

describe('parseError', () => {
  it('reads the documented envelope with field details and suggestions', () => {
    const e = parseError(400, {
      success: false,
      error: {
        code: 'validation_error',
        message: 'bad',
        details: { username: [{ code: 'username_taken', message: 'taken' }] },
        suggestions: ['ali1', 'ali2'],
      },
      detail: 'bad',
    });
    expect(e.code).toBe('validation_error');
    expect(e.details.username[0].code).toBe('username_taken');
    expect(e.suggestions).toEqual(['ali1', 'ali2']);
  });
  it('reads the legacy string shape of the throttle with retry_after', () => {
    const e = parseError(429, { error: 'rate_limit_exceeded', tier: 'login', retry_after: 42 });
    expect([e.code, e.retryAfter]).toEqual(['rate_limit_exceeded', 42]);
  });
  it('account_locked takes retry_after from the body, header as fallback', () => {
    expect(parseError(429, { error: { code: 'account_locked', retry_after: 60 } }).retryAfter).toBe(60);
    expect(parseError(429, { error: { code: 'account_locked' } }, '300').retryAfter).toBe(300);
  });
  it('falls back to status-based codes for non-JSON bodies', () => {
    expect(parseError(502, null).code).toBe('server_error');
    expect(parseError(401, '<html>').code).toBe('unauthorized');
    expect(parseError(418, undefined).code).toBe('generic');
  });
});

describe.each(['uz', 'ru', 'en', 'kaa', 'uz-Cyrl', 'uz-yangi'])('error texts (%s)', (l) => {
  const { t, has } = translator(l);
  it('every documented error code has a translation', () => {
    const missing = KNOWN_ERROR_CODES.filter((c) => !has(`errors.${c}`));
    expect(missing).toEqual([]);
  });
  it('maps a known code and falls back to errors.generic for unknown ones', () => {
    expect(errorText(parseError(400, { error: { code: 'invalid_credentials' } }), t, has)).toBe(
      t('errors.invalid_credentials'),
    );
    expect(errorText(parseError(400, { error: { code: 'brand_new_code' } }), t, has)).toBe(t('errors.generic'));
    expect(errorText(new Error('boom'), t, has)).toBe(t('errors.generic'));
  });
  it('fills {seconds} for lock messages', () => {
    expect(errorText(new ApiError(429, 'account_locked', '', {}, [], 90), t, has)).toContain('90');
  });
  it('translates per-field codes', () => {
    const err = parseError(400, {
      error: {
        code: 'validation_error',
        details: {
          password: [{ code: 'password_too_short', message: 'x' }],
          email: [{ code: 'zzz', message: 'y' }],
        },
      },
    });
    expect(fieldTexts(err, t, has)).toEqual({
      password: t('errors.password_too_short'),
      email: t('errors.invalid'),
    });
  });
});
