import type { NextConfig } from 'next';
import createNextIntlPlugin from 'next-intl/plugin';

const withNextIntl = createNextIntlPlugin('./src/i18n/request.ts');

// Server-side target of the same-origin proxy. Never exposed to the browser.
const API_URL = (process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8013').replace(/\/$/, '');

// Content-Security-Policy (audit C14), enforced. Next.js inlines its bootstrap scripts, so
// script-src keeps 'unsafe-inline' until a per-request nonce is set in src/proxy.ts (the
// pages are already rendered per request, so that is the next step). Everything else is
// locked down: no plugins, no framing, no foreign <base>, forms and fetches only to this
// origin (the /api proxy is same-origin).
const isDev = process.env.NODE_ENV === 'development';
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ''}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self'",
  `connect-src 'self'${isDev ? ' ws:' : ''}`,
  "frame-ancestors 'none'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join('; ');

const securityHeaders = [
  { key: 'Content-Security-Policy', value: csp },
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
];

const config: NextConfig = {
  // Django routes end with a slash; keep it intact when proxying /api/*.
  skipTrailingSlashRedirect: true,
  async headers() {
    // Not on /api and /accounts: those are proxied to Django, which sends its own policy.
    return [{ source: '/((?!api/|accounts/).*)', headers: securityHeaders }];
  },
  async rewrites() {
    return [
      // The slash variants must come first: ":path*" alone drops the trailing slash.
      { source: '/api/:path*/', destination: `${API_URL}/api/:path*/` },
      { source: '/api/:path*', destination: `${API_URL}/api/:path*` },
      // Google sign-in is a full-page navigation to allauth on the backend.
      { source: '/accounts/:path*/', destination: `${API_URL}/accounts/:path*/` },
      { source: '/accounts/:path*', destination: `${API_URL}/accounts/:path*` },
    ];
  },
};

export default withNextIntl(config);
