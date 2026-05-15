import axios from 'axios';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8013';

const apiClient = axios.create({
  baseURL: API_URL,
  withCredentials: true,
});

export const authService = {
  login: (email: string, password: string) =>
    apiClient.post('/accounts/api/auth/email/', 
      new URLSearchParams({ login: email, password }), 
      { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } }
    ),

  register: (regMethod: 'email' | 'username', data: Record<string, string>) =>
    apiClient.post('/accounts/api/auth/register/', 
      new URLSearchParams({ reg_method: regMethod, ...data }),
      { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } }
    ),

  logout: () =>
    apiClient.post('/accounts/api/auth/logout/'),

  sendOTP: (phone: string) =>
    apiClient.post('/accounts/api/auth/otp/send/', { phone }),

  verifyOTP: (phone: string, code: string) =>
    apiClient.post('/accounts/api/auth/otp/verify/', { phone, code }),
};

export default apiClient;
