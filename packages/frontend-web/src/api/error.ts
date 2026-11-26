/**
 * APIError — unified error class for backend response failures.
 *
 * Backend envelope (per ISSUE-204):
 *   {
 *     "success": false,
 *     "error": { "code": "validation_error", "message": "...", "details": {...} },
 *     "detail": "..."  // back-compat plain string
 *   }
 *
 * Step 14 will add a global handler (toast + form error mapping) on top of this.
 */

export interface ErrorEnvelope {
  success?: false;
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, string[] | string> | unknown;
  };
  detail?: string;
}

export class APIError extends Error {
  readonly status: number;
  readonly statusText: string;
  readonly body: ErrorEnvelope | null;

  constructor(status: number, statusText: string, body: unknown) {
    const envelope = (body && typeof body === 'object' ? (body as ErrorEnvelope) : null) ?? null;
    const message =
      envelope?.error?.message ??
      envelope?.detail ??
      (typeof body === 'string' ? body : `HTTP ${status} ${statusText}`);
    super(message);
    this.name = 'APIError';
    this.status = status;
    this.statusText = statusText;
    this.body = envelope;
  }

  /** Machine-readable code from envelope (e.g. 'validation_error', 'rate_limit_exceeded'). */
  get code(): string | undefined {
    return this.body?.error?.code;
  }

  /** Field-level validation errors (DRF style: { field: [msg1, msg2] }). */
  get fieldErrors(): Record<string, string[]> {
    const details = this.body?.error?.details;
    if (!details || typeof details !== 'object') return {};
    const out: Record<string, string[]> = {};
    for (const [field, errs] of Object.entries(details as Record<string, unknown>)) {
      if (Array.isArray(errs)) {
        out[field] = errs.filter((e): e is string => typeof e === 'string');
      } else if (typeof errs === 'string') {
        out[field] = [errs];
      }
    }
    return out;
  }

  /** Convenience: is this a rate-limit (HTTP 429)? */
  get isRateLimit(): boolean {
    return this.status === 429 || this.code === 'rate_limit_exceeded';
  }

  /** Convenience: is this an auth failure (HTTP 401)? */
  get isUnauthorized(): boolean {
    return this.status === 401 || this.code === 'authentication_required';
  }
}
