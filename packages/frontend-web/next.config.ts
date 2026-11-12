import type { NextConfig } from 'next';
import createNextIntlPlugin from 'next-intl/plugin';

const withNextIntl = createNextIntlPlugin('./src/i18n/request.ts');

// Server-side target of the same-origin proxy. Never exposed to the browser.
const API_URL = (process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8013').replace(/\/$/, '');

const config: NextConfig = {
  // Django routes end with a slash; keep it intact when proxying /api/*.
  skipTrailingSlashRedirect: true,
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
