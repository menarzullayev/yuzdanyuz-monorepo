export interface FieldError {
  code: string;
  message: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: Record<string, FieldError[]> = {},
    public suggestions: string[] = [],
    public retryAfter?: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

type Json = Record<string, unknown>;
const isObj = (v: unknown): v is Json => !!v && typeof v === 'object' && !Array.isArray(v);

function defaultCode(status: number): string {
  if (status >= 500) return 'server_error';
  if (status === 401) return 'unauthorized';
  if (status === 403) return 'forbidden';
  if (status === 404) return 'not_found';
  return 'generic';
}

/**
 * Normalises the backend envelope, the legacy `{error: "string"}` shape
 * (Telegram, throttle) and non-JSON bodies into one ApiError.
 */
export function parseError(status: number, body: unknown, retryAfterHeader?: string | null): ApiError {
  const header = retryAfterHeader ? Number(retryAfterHeader) : NaN;
  let code = defaultCode(status);
  let message = '';
  let details: Record<string, FieldError[]> = {};
  let suggestions: string[] = [];
  let retryAfter = Number.isFinite(header) ? header : undefined;

  if (isObj(body)) {
    const err = body.error;
    if (typeof err === 'string') {
      code = err;
      if (typeof body.retry_after === 'number') retryAfter = body.retry_after;
    } else if (isObj(err)) {
      if (typeof err.code === 'string') code = err.code;
      if (typeof err.message === 'string') message = err.message;
      if (typeof err.retry_after === 'number') retryAfter = err.retry_after;
      if (Array.isArray(err.suggestions)) {
        suggestions = err.suggestions.filter((s): s is string => typeof s === 'string');
      }
      if (isObj(err.details)) {
        details = Object.fromEntries(
          Object.entries(err.details).map(([field, list]) => [
            field,
            (Array.isArray(list) ? list : []).filter(isObj).map((e) => ({
              code: String(e.code ?? 'invalid'),
              message: String(e.message ?? ''),
            })),
          ]),
        );
      }
    }
    if (!message && typeof body.detail === 'string') message = body.detail;
  }
  return new ApiError(status, code, message, details, suggestions, retryAfter);
}

export function networkError(): ApiError {
  return new ApiError(0, 'network', 'network');
}

export type Translate = (key: string, values?: Record<string, string | number>) => string;

/**
 * Picks the translated text for an error code. `has` tells whether a key exists, so unknown
 * server codes fall back to errors.generic instead of throwing at runtime.
 */
export function errorText(err: unknown, t: Translate, has: (key: string) => boolean): string {
  if (!(err instanceof ApiError)) return t('errors.generic');
  const key = `errors.${err.code}`;
  return has(key) ? t(key, { seconds: err.retryAfter ?? 60 }) : t('errors.generic');
}

/** Translated messages per form field (`details` of a validation_error), first code per field. */
export function fieldTexts(err: unknown, t: Translate, has: (key: string) => boolean): Record<string, string> {
  const out: Record<string, string> = {};
  if (!(err instanceof ApiError)) return out;
  for (const [field, list] of Object.entries(err.details)) {
    const first = list[0];
    if (!first) continue;
    const key = `errors.${first.code}`;
    out[field] = has(key) ? t(key) : t('errors.invalid');
  }
  return out;
}

/** Every code the auth API documents; tests make sure each has a translation. */
export const KNOWN_ERROR_CODES = [
  'validation_error', 'invalid_credentials', 'invalid_code', 'account_locked', 'rate_limit_exceeded',
  'origin_not_allowed', 'unauthorized', 'forbidden', 'not_found', 'invalid_token', 'invalid_ticket',
  'not_required', 'last_method', 'not_linked', 'already_enabled', 'consent_required', 'invalid_invite',
  'telegram_not_verified', 'telegram_in_use', 'already_linked', 'no_telegram',
  'username_change_not_allowed', 'username_invalid', 'username_reserved', 'username_taken',
  'email_invalid', 'email_taken', 'first_name_length', 'last_name_length', 'password_required',
  'password_too_short', 'password_too_long', 'password_too_many_bytes', 'password_repeated_char',
  'password_too_common', 'password_entirely_numeric', 'password_local_word',
  'password_equals_username', 'password_too_similar', 'server_error', 'network',
] as const;
