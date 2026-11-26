/**
 * Toast wrapper — wraps `sonner` with brand-aligned defaults + APIError integration.
 *
 * Usage:
 *   import { toast } from '@/lib/toast';
 *
 *   toast.success("Saqlandi");
 *   toast.error("Xato yuz berdi");
 *   toast.fromApiError(err);   // unified envelope → friendly message + auto-redirect on 401
 */

import { toast as sonnerToast } from 'sonner';

import { APIError } from '@/api/error';

interface ToastOpts {
  description?: string;
  duration?: number;
}

export const toast = {
  success(message: string, opts?: ToastOpts) {
    return sonnerToast.success(message, opts);
  },

  error(message: string, opts?: ToastOpts) {
    return sonnerToast.error(message, opts);
  },

  info(message: string, opts?: ToastOpts) {
    return sonnerToast(message, opts);
  },

  warning(message: string, opts?: ToastOpts) {
    return sonnerToast.warning(message, opts);
  },

  /**
   * Display an APIError as a toast with sensible UX.
   * - 401 → "Tizimga qayta kiring" (login redirect handled by caller via isUnauthorized)
   * - 429 → "Juda ko'p so'rov. {retry_after}s kuting"
   * - 5xx → "Server xatosi. Qayta urinib ko'ring"
   * - Other → backend's error.message yoki generic
   */
  fromApiError(err: unknown) {
    if (!(err instanceof APIError)) {
      return sonnerToast.error("Kutilmagan xato yuz berdi.", {
        description: String(err),
      });
    }

    if (err.isUnauthorized) {
      return sonnerToast.error("Tizimga qayta kiring", {
        description: "Sessiya tugagan yoki ruxsat yo'q.",
      });
    }

    if (err.isRateLimit) {
      const retry = (err.body as { retry_after?: number } | null)?.retry_after;
      return sonnerToast.warning("Juda ko'p so'rov", {
        description: retry
          ? `${retry} soniyadan keyin qayta urinib ko'ring.`
          : 'Biroz kutib, qayta urinib ko\'ring.',
      });
    }

    if (err.status >= 500) {
      return sonnerToast.error('Server xatosi', {
        description: 'Qayta urinib ko\'ring. Muammo davom etsa, support bilan bog\'laning.',
      });
    }

    return sonnerToast.error(err.message || 'Xato yuz berdi', {
      description: err.code ? `Kod: ${err.code}` : undefined,
    });
  },

  /**
   * Convenience: map APIError.fieldErrors → react-hook-form's setError.
   * Usage in form submit handler:
   *   try { await api(...) }
   *   catch (err) { toast.toFormErrors(err, form.setError) }
   */
  toFormErrors(
    err: unknown,
    setFieldError: (name: string, error: { type: string; message: string }) => void,
  ): boolean {
    if (!(err instanceof APIError)) return false;
    const fieldErrors = err.fieldErrors;
    if (Object.keys(fieldErrors).length === 0) return false;
    for (const [field, messages] of Object.entries(fieldErrors)) {
      const msg = messages[0] ?? 'Xato';
      setFieldError(field, { type: 'server', message: msg });
    }
    return true;
  },
};
