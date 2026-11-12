import { api } from './client';
import type {
  Linked,
  LoginResult,
  Me,
  OnboardingOptions,
  OnboardingState,
  RegisterInput,
  SessionInfo,
  TgInit,
  TgStatus,
  TwoFactorState,
  UsernameCheck,
  User,
} from './types';

const post = <T>(path: string, body?: unknown, noRefresh = true) =>
  api<T>(path, { method: 'POST', body: body ?? {}, noRefresh });

export const authApi = {
  me: () => api<Me>('/me/', { noRefresh: false }),
  login: (b: { username: string; password: string; remember: boolean; code?: string }) =>
    post<LoginResult>('/login/', b),
  changeRequiredPassword: (b: { change_token: string; new_password: string; remember: boolean }) =>
    post<{ user: User }>('/password/change-required/', b),
  register: (b: RegisterInput) => post<{ user: User }>('/register/', b),
  logout: () => post<{ ok: true }>('/logout/'),
  usernameCheck: (username: string) =>
    api<UsernameCheck>('/username/check/', { query: { username }, noRefresh: true }),

  tgInit: () => post<TgInit>('/tg/init/'),
  tgStatus: (token: string) => api<{ status: TgStatus }>('/tg/status/', { query: { token }, noRefresh: true }),
  tgComplete: (token: string, remember: boolean) => post<{ user: User }>('/tg/complete/', { token, remember }),

  recoveryTelegram: (username: string) => post<{ ok: true; message: string }>('/recovery/telegram/', { username }),
  recoveryRequest: (username: string, contact: string) =>
    post<{ ok: true }>('/recovery/request/', { username, contact }),
  recoveryConfirm: (token: string, new_password: string) =>
    post<{ ok: true }>('/recovery/confirm/', { token, new_password }),

  sessions: () => api<{ sessions: SessionInfo[] }>('/sessions/'),
  revokeSession: (id: string) => api<{ ok: true }>(`/sessions/${id}/`, { method: 'DELETE' }),
  revokeOthers: () => post<{ ok: true; revoked: number }>('/sessions/revoke-others/', {}, false),

  twoFactor: () => api<TwoFactorState>('/2fa/'),
  twoFactorSetup: () => post<{ secret: string; otpauth_uri: string }>('/2fa/setup/', {}, false),
  twoFactorConfirm: (code: string) =>
    post<{ enabled: true; backup_codes: string[] }>('/2fa/confirm/', { code }, false),
  twoFactorDisable: (b: { password?: string; code: string }) =>
    post<{ enabled: false }>('/2fa/disable/', b, false),
  backupCodes: (code: string) => post<{ backup_codes: string[] }>('/2fa/backup-codes/regenerate/', { code }, false),

  linked: () => api<Linked>('/linked/'),
  linkTelegramStart: () => post<TgInit>('/linked/telegram/start/', {}, false),
  unlink: (provider: 'telegram' | 'google') => api<Linked>(`/linked/${provider}/`, { method: 'DELETE' }),

  onboardingOptions: () => api<OnboardingOptions>('/onboarding/options/', { noRefresh: true }),
  onboarding: () => api<OnboardingState>('/onboarding/'),
  saveOnboarding: (b: Record<string, unknown>) => api<OnboardingState>('/onboarding/', { method: 'PATCH', body: b }),
  joinOrg: (code: string, consent: boolean) =>
    post<{ organization: { id: string; name: string } }>('/onboarding/org-invite/', { code, consent }, false),
  withdrawOrg: (id: string) => api<{ ok: true }>(`/onboarding/org-consent/${id}/`, { method: 'DELETE' }),
};
