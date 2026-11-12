import { PLATFORM_WORDS } from '@/config/brand';

export type HintId = 'length' | 'bytes' | 'notNumeric' | 'notRepeated' | 'noLocalWords' | 'notSimilar';

export interface PasswordContext {
  username?: string;
  firstName?: string;
  lastName?: string;
  email?: string;
}

export interface Hint {
  id: HintId;
  ok: boolean;
}

const norm = (s: string) => s.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');

function utf8Bytes(s: string): number {
  return new TextEncoder().encode(s).length;
}

/** Cheap similarity: the password contains an attribute (>=4 chars) or the other way round. */
function isSimilar(pw: string, attrs: string[]): boolean {
  const p = norm(pw);
  if (!p) return false;
  return attrs.map(norm).some((a) => a.length >= 4 && (p.includes(a) || (p.length >= 4 && a.includes(p))));
}

/**
 * Client-side mirror of the server policy, for live hints only.
 * The server stays authoritative (it also checks Django's common-password list).
 */
export function checkPassword(pw: string, ctx: PasswordContext = {}): Hint[] {
  const lower = pw.toLowerCase();
  const attrs = [ctx.username, ctx.firstName, ctx.lastName, ctx.email?.split('@')[0]].filter(
    (v): v is string => !!v,
  );
  const hasRepeat = pw.length > 0 && [...pw].every((c) => c === pw[0]);
  const localWord = PLATFORM_WORDS.some((w) => lower.includes(w)) || /123456|654321/.test(lower);
  return [
    { id: 'length', ok: pw.length >= 8 && pw.length <= 128 },
    { id: 'bytes', ok: utf8Bytes(pw) <= 72 },
    { id: 'notNumeric', ok: pw.length > 0 && !/^\p{N}+$/u.test(pw) },
    { id: 'notRepeated', ok: pw.length > 0 && !hasRepeat },
    { id: 'noLocalWords', ok: pw.length > 0 && !localWord },
    {
      id: 'notSimilar',
      ok: pw.length > 0 && lower !== (ctx.username ?? '').toLowerCase() && !isSimilar(pw, attrs),
    },
  ];
}

export function passwordValid(pw: string, ctx?: PasswordContext): boolean {
  return checkPassword(pw, ctx).every((h) => h.ok);
}

export type Strength = 'weak' | 'ok' | 'strong';

export function strength(pw: string, ctx?: PasswordContext): Strength {
  if (!passwordValid(pw, ctx)) return 'weak';
  const classes = [/\p{Ll}/u, /\p{Lu}/u, /\p{N}/u, /[^\p{L}\p{N}]/u].filter((r) => r.test(pw)).length;
  return pw.length >= 12 && classes >= 3 ? 'strong' : 'ok';
}
