# Frontend Implementation Plan: Next.js + React Native Monorepo

**Date**: 2026-05-15  
**Status**: 🔵 IN PROGRESS  
**Target**: Full Next.js web + PWA + TMA setup in 4-6 hours

---

## 📋 Executive Summary

Monorepo structure setup uchun:
```
/home/hsm/apps/yuzdanyuz-monorepo/
├── packages/
│   ├── backend/           (Django REST API)
│   ├── frontend-web/      (Next.js 15 + React 19)
│   ├── frontend-mobile/   (React Native - future)
│   └── shared/            (Shared TypeScript logic)
```

**Why Monorepo?**
- ✅ Code sharing between web + mobile
- ✅ Single CI/CD pipeline
- ✅ Professional industry standard
- ✅ Easier React Native integration later

---

## 🗺️ Detailed Implementation Steps

### Phase 1: Environment Check & Setup (15-30 min)

#### Step 1.1: Verify Node.js Installation
```bash
# Check if Node.js exists
node --version
npm --version

# If not installed:
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# Verify
node --version  # Should be v20.x+
npm --version   # Should be 10.x+
```

#### Step 1.2: Create Monorepo Structure
```bash
# Create main monorepo folder
mkdir -p /home/hsm/apps/yuzdanyuz-monorepo
cd /home/hsm/apps/yuzdanyuz-monorepo

# Create package structure
mkdir -p packages/{backend,frontend-web,frontend-mobile,shared}

# Initialize root
npm init -y
```

#### Step 1.3: Backup & Move Django Backend
```bash
# Backup original (just in case)
cp -r /home/hsm/apps/yuzdanyuz /home/hsm/apps/yuzdanyuz-backup-$(date +%s)

# Move to monorepo
mv /home/hsm/apps/yuzdanyuz /home/hsm/apps/yuzdanyuz-monorepo/packages/backend

# Verify
ls -la /home/hsm/apps/yuzdanyuz-monorepo/packages/backend/manage.py
```

**Checkpoint**: ✅ Node.js ready, monorepo structure created, Django moved

---

### Phase 2: Shared Package Setup (30-45 min)

#### Step 2.1: Initialize Shared Package
```bash
cd /home/hsm/apps/yuzdanyuz-monorepo/packages/shared

# Create package.json
cat > package.json << 'EOF'
{
  "name": "@yuzdanyuz/shared",
  "version": "1.0.0",
  "description": "Shared TypeScript utilities for web + mobile",
  "main": "dist/index.js",
  "types": "dist/index.d.ts",
  "scripts": {
    "build": "tsc",
    "dev": "tsc --watch"
  },
  "dependencies": {
    "axios": "^1.6.0",
    "jwt-decode": "^4.0.0"
  },
  "devDependencies": {
    "typescript": "^5.0.0"
  }
}
EOF

npm install
```

#### Step 2.2: Setup TypeScript Configuration
```bash
cat > tsconfig.json << 'EOF'
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "ESNext",
    "lib": ["ES2020"],
    "moduleResolution": "bundler",
    "declaration": true,
    "outDir": "./dist",
    "rootDir": "./src",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true
  },
  "include": ["src/**/*"],
  "exclude": ["node_modules", "dist"]
}
EOF
```

#### Step 2.3: Create Shared API Client
```bash
mkdir -p src/{api,auth,types,utils,hooks}

# API Client
cat > src/api/client.ts << 'EOF'
import axios, { AxiosInstance } from 'axios';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8013';

export const apiClient: AxiosInstance = axios.create({
  baseURL: API_URL,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Auto-attach JWT from cookies
apiClient.interceptors.request.use((config) => {
  const token = getCookie('access_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Handle auth errors
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // Token expired - redirect to login
      if (typeof window !== 'undefined') {
        window.location.href = '/login';
      }
    }
    return Promise.reject(error);
  }
);

function getCookie(name: string): string | null {
  if (typeof document === 'undefined') return null;
  const value = `; ${document.cookie}`;
  const parts = value.split(`; ${name}=`);
  if (parts.length === 2) return parts.pop()?.split(';').shift() || null;
  return null;
}

export default apiClient;
EOF

# API Types
cat > src/types/api.ts << 'EOF'
export interface AuthResponse {
  access_token?: string;
  refresh_token?: string;
  user: User;
}

export interface User {
  id: string;
  username: string;
  email: string;
  first_name: string;
  last_name: string;
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

export interface OTPResponse {
  phone: string;
  expires_in: number;
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
EOF

# Auth Utils
cat > src/auth/jwt.ts << 'EOF'
import jwtDecode from 'jwt-decode';

export interface JWTPayload {
  sub: string;
  current_org?: string;
  iat: number;
  exp: number;
  type: 'access' | 'refresh';
}

export function decodeJWT(token: string): JWTPayload | null {
  try {
    return jwtDecode<JWTPayload>(token);
  } catch {
    return null;
  }
}

export function isTokenExpired(token: string): boolean {
  const decoded = decodeJWT(token);
  if (!decoded) return true;
  return Date.now() >= decoded.exp * 1000;
}

export function getCookie(name: string): string | null {
  if (typeof document === 'undefined') return null;
  const value = `; ${document.cookie}`;
  const parts = value.split(`; ${name}=`);
  if (parts.length === 2) return parts.pop()?.split(';').shift() || null;
  return null;
}

export function getAccessToken(): string | null {
  return getCookie('access_token');
}

export function isAuthenticated(): boolean {
  const token = getAccessToken();
  return token !== null && !isTokenExpired(token);
}
EOF

# Build shared package
npm run build
```

**Checkpoint**: ✅ Shared package created with API client, types, auth utilities

---

### Phase 3: Next.js Frontend Setup (1-1.5 hours)

#### Step 3.1: Create Next.js 15 Project
```bash
cd /home/hsm/apps/yuzdanyuz-monorepo/packages/frontend-web

# Create Next.js with TypeScript + Tailwind
npx create-next-app@15 . \
  --typescript \
  --tailwind \
  --eslint \
  --app \
  --no-git \
  --src-dir

# Install additional dependencies
npm install \
  axios \
  zustand \
  jwt-decode \
  classnames \
  date-fns
```

#### Step 3.2: Setup Environment Variables
```bash
cat > .env.local << 'EOF'
# API Configuration
NEXT_PUBLIC_API_URL=http://127.0.0.1:8013
NEXT_PUBLIC_APP_NAME=YuzDanYuz

# Feature Flags
NEXT_PUBLIC_ENABLE_PWA=true
NEXT_PUBLIC_ENABLE_TMA=true

# Development
NEXT_PUBLIC_DEBUG=false
EOF
```

#### Step 3.3: Create Directory Structure
```bash
mkdir -p src/{app,components,services,hooks,lib,types,styles}

# Import design components from backup
cp -r /home/hsm/apps/yuzdanyuz-monorepo/packages/backend/design/claudeDesign/src/app/components/* ./src/components/ 2>/dev/null || echo "Design components will be added manually"

# Create essential folders
mkdir -p src/{middleware,contexts,stores}
```

#### Step 3.4: Setup API Service Layer
```bash
cat > src/services/auth.ts << 'EOF'
import { apiClient } from '@yuzdanyuz/shared/api/client';
import { AuthResponse, LoginRequest, RegisterRequest } from '@yuzdanyuz/shared/types/api';

export const authService = {
  login: (data: LoginRequest) =>
    apiClient.post<AuthResponse>('/accounts/api/auth/email/', new URLSearchParams(data)),

  register: (data: RegisterRequest) =>
    apiClient.post<AuthResponse>('/accounts/api/auth/register/', new URLSearchParams(data)),

  logout: () =>
    apiClient.post('/accounts/api/auth/logout/'),

  refreshToken: () =>
    apiClient.post('/accounts/api/auth/refresh/'),

  sendOTP: (phone: string) =>
    apiClient.post('/accounts/api/auth/otp/send/', JSON.stringify({ phone }), {
      headers: { 'Content-Type': 'application/json' },
    }),

  verifyOTP: (phone: string, code: string) =>
    apiClient.post('/accounts/api/auth/otp/verify/', JSON.stringify({ phone, code }), {
      headers: { 'Content-Type': 'application/json' },
    }),
};
EOF

cat > src/services/api.ts << 'EOF'
import { apiClient } from '@yuzdanyuz/shared/api/client';

export const api = {
  get: apiClient.get,
  post: apiClient.post,
  put: apiClient.put,
  delete: apiClient.delete,
  patch: apiClient.patch,
};

export default apiClient;
EOF
```

#### Step 3.5: Create Auth Hooks
```bash
cat > src/hooks/useAuth.ts << 'EOF'
import { useCallback, useEffect, useState } from 'react';
import { getAccessToken, isTokenExpired } from '@yuzdanyuz/shared/auth/jwt';
import { authService } from '@/services/auth';
import { User } from '@yuzdanyuz/shared/types/api';

export function useAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isAuthenticated, setIsAuthenticated] = useState(false);

  useEffect(() => {
    const token = getAccessToken();
    if (token && !isTokenExpired(token)) {
      setIsAuthenticated(true);
      // Fetch user profile
      // TODO: Implement GET /accounts/api/user/profile/
    }
    setIsLoading(false);
  }, []);

  const logout = useCallback(async () => {
    await authService.logout();
    setUser(null);
    setIsAuthenticated(false);
  }, []);

  return { user, isLoading, isAuthenticated, logout };
}
EOF
```

**Checkpoint**: ✅ Next.js project created with API integration, env vars, hooks

---

### Phase 4: Core Pages & Components (1-1.5 hours)

#### Step 4.1: Create Layout
```bash
cat > src/app/layout.tsx << 'EOF'
import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'YuzDanYuz — DTM Simulator',
  description: 'O\'zbekistondagi eng katta DTM simulator va ta\'lim platformasi',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="uz">
      <body className="bg-white dark:bg-gray-900">
        {children}
      </body>
    </html>
  );
}
EOF
```

#### Step 4.2: Create Core Pages
```bash
# Home/Dashboard page
mkdir -p src/app/{login,dashboard,profile}

# Login page
cat > src/app/login/page.tsx << 'EOF'
'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { authService } from '@/services/auth';

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError('');

    try {
      await authService.login({ login: email, password });
      router.push('/dashboard');
    } catch (err: any) {
      setError(err.response?.data?.message || 'Login failed');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="w-full max-w-md bg-white p-8 rounded-lg shadow">
        <h1 className="text-2xl font-bold mb-6">YuzDanYuz Kirish</h1>

        {error && (
          <div className="mb-4 p-4 bg-red-100 text-red-700 rounded">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-2">
              Email yoki username
            </label>
            <input
              type="text"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full px-4 py-2 border rounded-lg"
              required
            />
          </div>

          <div>
            <label className="block text-sm font-medium mb-2">Parol</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full px-4 py-2 border rounded-lg"
              required
            />
          </div>

          <button
            type="submit"
            disabled={isLoading}
            className="w-full bg-blue-600 text-white py-2 rounded-lg hover:bg-blue-700 disabled:opacity-50"
          >
            {isLoading ? 'Kirish...' : 'Kirish'}
          </button>
        </form>
      </div>
    </div>
  );
}
EOF

# Dashboard page (protected)
cat > src/app/dashboard/page.tsx << 'EOF'
'use client';

import { useAuth } from '@/hooks/useAuth';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';

export default function DashboardPage() {
  const { user, isLoading, isAuthenticated } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!isLoading && !isAuthenticated) {
      router.push('/login');
    }
  }, [isLoading, isAuthenticated, router]);

  if (isLoading) {
    return <div className="flex items-center justify-center h-screen">Yuklanmoqda...</div>;
  }

  return (
    <div className="min-h-screen bg-gray-50 p-4">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-3xl font-bold mb-4">
          Assalomu alaikum, {user?.first_name || 'Foydalanuvchi'}!
        </h1>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="bg-white p-6 rounded-lg shadow">
            <h2 className="text-lg font-semibold">Testlar</h2>
            <p className="text-3xl font-bold text-blue-600">0</p>
          </div>

          <div className="bg-white p-6 rounded-lg shadow">
            <h2 className="text-lg font-semibold">Ball</h2>
            <p className="text-3xl font-bold text-green-600">0</p>
          </div>

          <div className="bg-white p-6 rounded-lg shadow">
            <h2 className="text-lg font-semibold">Reyting</h2>
            <p className="text-3xl font-bold text-purple-600">-</p>
          </div>
        </div>
      </div>
    </div>
  );
}
EOF
```

**Checkpoint**: ✅ Core pages created (login, dashboard), auth flow working

---

### Phase 5: PWA Setup (30-45 min)

#### Step 5.1: Add PWA Support
```bash
npm install next-pwa

cat > next.config.js << 'EOF'
const withPWA = require('next-pwa')({
  dest: 'public',
  register: true,
  skipWaiting: true,
});

module.exports = withPWA({
  reactStrictMode: true,
});
EOF
```

#### Step 5.2: Create Manifest
```bash
cat > public/manifest.json << 'EOF'
{
  "name": "YuzDanYuz — DTM Simulator",
  "short_name": "YuzDanYuz",
  "description": "O'zbekistondagi DTM simulator va ta'lim platformasi",
  "start_url": "/",
  "scope": "/",
  "display": "standalone",
  "background_color": "#ffffff",
  "theme_color": "#2563eb",
  "icons": [
    {
      "src": "/icon-192x192.png",
      "sizes": "192x192",
      "type": "image/png"
    },
    {
      "src": "/icon-512x512.png",
      "sizes": "512x512",
      "type": "image/png"
    }
  ]
}
EOF
```

#### Step 5.3: Update Layout with PWA Meta Tags
```bash
cat >> src/app/layout.tsx << 'EOF'
// Add to <head>:
<meta name="theme-color" content="#2563eb" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<link rel="manifest" href="/manifest.json" />
<link rel="icon" href="/favicon.ico" />
EOF
```

**Checkpoint**: ✅ PWA configured, installable on any device

---

### Phase 6: Environment & Deployment (30-45 min)

#### Step 6.1: Update Production Scripts
```bash
cat > /home/hsm/run-production-all.sh << 'EOF'
#!/bin/bash
# Run both Django backend + Next.js frontend

set +e
cd /home/hsm/apps/yuzdanyuz-monorepo

echo "=========================================="
echo "🚀 YuzDanYuz Full Stack (Django + Next.js)"
echo "=========================================="
echo ""

# Django Backend
echo "[$(date '+%H:%M:%S')] 🔧 Starting Django Backend..."
cd packages/backend
source venv/bin/activate
python manage.py runserver 127.0.0.1:8013 &
DJANGO_PID=$!
echo "[$(date '+%H:%M:%S')] ✅ Django running (PID: $DJANGO_PID)"
sleep 3

# Next.js Frontend
echo "[$(date '+%H:%M:%S')] 🔧 Starting Next.js Frontend..."
cd /home/hsm/apps/yuzdanyuz-monorepo/packages/frontend-web
npm run dev &
NEXTJS_PID=$!
echo "[$(date '+%H:%M:%S')] ✅ Next.js running (PID: $NEXTJS_PID)"

echo ""
echo "=========================================="
echo "✅ Server ishga tushdi"
echo "=========================================="
echo ""
echo "📍 Backend API: http://127.0.0.1:8013/"
echo "🌐 Frontend Web: http://127.0.0.1:3000/"
echo ""
echo "Ctrl+C - to'xtatish"
echo "=========================================="
echo ""

# Wait for both processes
wait
EOF

chmod +x /home/hsm/run-production-all.sh
```

#### Step 6.2: Build & Deploy
```bash
cd /home/hsm/apps/yuzdanyuz-monorepo/packages/frontend-web

# Build for production
npm run build

# Start production server
npm run start  # Runs on :3000
```

**Checkpoint**: ✅ Production setup ready, both services can run together

---

## 📊 Implementation Checklist

### Phase 1: Environment ✅
- [ ] Node.js v20+ installed
- [ ] Monorepo folder structure created
- [ ] Django backend moved
- [ ] Git initialized

### Phase 2: Shared Package ✅
- [ ] TypeScript configured
- [ ] API client created
- [ ] Types defined
- [ ] Auth utilities written
- [ ] Package built

### Phase 3: Next.js Setup ✅
- [ ] Next.js 15 project created
- [ ] Environment variables configured
- [ ] Directory structure organized
- [ ] API services created
- [ ] Hooks implemented

### Phase 4: Pages & Components ✅
- [ ] Layout created
- [ ] Login page created
- [ ] Dashboard page created
- [ ] Auth flow tested

### Phase 5: PWA ✅
- [ ] PWA configured
- [ ] Manifest created
- [ ] Meta tags added
- [ ] Offline support enabled

### Phase 6: Deployment ✅
- [ ] Production scripts created
- [ ] Build tested
- [ ] Both services running together
- [ ] Environment validated

---

## 🧪 Testing & Validation

```bash
# Test backend + frontend together
/home/hsm/run-production-all.sh

# In browser:
# Frontend: http://127.0.0.1:3000/
# Login with test credentials (from Django fixtures)
# Dashboard should load

# API calls from frontend to backend should work
# JWT cookies should be set
# PWA should be installable
```

---

## 📝 Next: React Native Setup

After Next.js is complete and tested:
1. Initialize React Native project in `packages/frontend-mobile/`
2. Setup shared code imports
3. Create iOS + Android builds
4. Deploy to App Store + Google Play

---

## 🔗 Resources

- [Next.js 15 Docs](https://nextjs.org/docs)
- [React 19 Docs](https://react.dev)
- [TypeScript Handbook](https://www.typescriptlang.org/docs/)
- [Tailwind CSS](https://tailwindcss.com/docs)
- [Monorepo Best Practices](https://monorepo.tools/)

---

**Last Updated**: 2026-05-15  
**Status**: 🔴 READY TO IMPLEMENT
