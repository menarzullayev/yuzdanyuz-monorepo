export interface User {
  id: string;
  username: string;
  email: string;
  first_name?: string;
  last_name?: string;
  phone_number?: string;
  telegram_id?: number;
  user_type: 'platform_admin' | 'platform_staff' | 'b2c' | 'student' | 'teacher' | 'manager' | 'owner';
  is_active: boolean;
  primary_organization?: Organization;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  type: 'b2c' | 'b2b' | 'educational';
  single_device_policy: boolean;
}

export interface AuthResponse {
  access_token?: string;
  refresh_token?: string;
  user?: User;
}

export interface LoginRequest {
  login: string;
  password: string;
}

export interface RegisterRequest {
  reg_method: 'email' | 'username';
  email?: string;
  username?: string;
  password: string;
  password_confirm: string;
}

export interface OTPRequest {
  phone: string;
}

export interface OTPVerifyRequest {
  phone: string;
  code: string;
}
