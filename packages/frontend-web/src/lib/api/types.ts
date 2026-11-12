export interface User {
  id: string;
  username: string;
  first_name: string;
  last_name: string;
  display_name: string;
  email: string | null;
  email_verified: boolean;
  phone_number: string | null;
  avatar: string | null;
  preferred_lang: string;
  user_type: string;
  is_staff: boolean;
  is_superuser: boolean;
  has_password: boolean;
  username_change_available: boolean;
  telegram: { id: number; username: string | null; photo_url: string | null } | null;
  primary_organization: { id: string; name: string; slug: string } | null;
}

export interface Me extends User {
  is_profile_complete: boolean;
  two_factor_enabled: boolean;
  onboarding: { is_complete: boolean; missing: string[] };
}

export type LoginResult =
  | { user: User }
  | { need_code: true }
  | { password_change_required: true; change_token: string };

export interface RegisterInput {
  first_name: string;
  last_name: string;
  username: string;
  password: string;
  email?: string;
  remember: boolean;
  acquisition?: Record<string, string>;
}

export interface UsernameCheck {
  username: string;
  valid: boolean;
  available: boolean;
  reason?: 'username_invalid' | 'username_reserved' | 'username_taken';
  suggestions?: string[];
}

export interface TgInit {
  token: string;
  deep_link: string;
  expires_in: number;
}

export type TgStatus = 'pending' | 'waiting' | 'verified' | 'expired' | 'confirming' | 'linked' | 'conflict';

export interface SessionInfo {
  id: string;
  device_label: string;
  browser: string;
  os: string;
  device_type: string;
  country: string | null;
  city: string | null;
  ip_address: string | null;
  remember: boolean;
  created_at: string;
  last_used_at: string;
  is_current: boolean;
}

export interface TwoFactorState {
  enabled: boolean;
  backup_codes_remaining: number;
}

export interface Linked {
  username: string;
  has_password: boolean;
  telegram: { linked: boolean; username: string | null };
  google: { linked: boolean; email: string | null };
  can_unlink: { telegram: boolean; google: boolean };
  google_connect_url: string;
}

export interface Goal {
  code: string;
  name_uz: string;
  name_ru: string;
  name_en: string;
  requires_direction: boolean;
}

export interface Direction {
  code: string;
  name_uz: string;
  name_ru: string;
  name_en: string;
  passing_score: { year: number; grant: number; contract: number } | null;
}

export interface Region {
  code: string;
  name_uz: string;
  name_ru: string;
  name_en: string;
  districts: { id: number; name_uz: string; name_ru: string }[];
}

export interface OnboardingOptions {
  goals: Goal[];
  dtm_directions: Direction[];
  regions: Region[];
  exam_calendar: { exam_goal: string; title: string; date: string; registration_deadline: string | null }[];
  test_languages: { code: string; name: string }[];
  target_grades: string[];
  certificate_types: { code: string; name: string }[];
  acquisition_answers: string[];
  target_score_max: number;
  graduation_year_range: [number, number];
}

export interface Certificate {
  cert_type: string;
  subject: string | null;
  level: string;
  date: string;
}

export interface OnboardingState {
  is_complete: boolean;
  missing: string[];
  goals: string[];
  primary_goal: string | null;
  dtm_direction: string | null;
  test_language: string | null;
  target_score: number | null;
  target_grade: string | null;
  exam_date: string | null;
  graduation_year: number | null;
  region: string | null;
  district: number | null;
  birth_date: string | null;
  parent_telegram: string | null;
  acquisition_answer: string | null;
  acquisition_note: string | null;
  certificates: Certificate[];
  org_consents: { organization_id: string; organization_name: string; consented_at: string }[];
}
