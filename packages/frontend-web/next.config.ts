import type { NextConfig } from 'next';
import createNextIntlPlugin from 'next-intl/plugin';

const withNextIntl = createNextIntlPlugin('./src/i18n/request.ts');

const nextConfig: NextConfig = {
  // Production runs under /yuzdanyuz/ subpath behind PHP-FPM proxy.
  // basePath/assetPrefix not set yet — Vercel direct deploy first,
  // then enable for self-hosted deploy under /yuzdanyuz/.
};

export default withNextIntl(nextConfig);
