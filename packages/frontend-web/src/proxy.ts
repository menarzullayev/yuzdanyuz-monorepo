import createMiddleware from 'next-intl/middleware';
import { routing } from './i18n/routing';

export default createMiddleware(routing);

export const config = {
  // Skip the API proxy, Google OAuth, static files (anything with a dot) and Next internals.
  matcher: ['/((?!api|accounts|_next|_vercel|mascot|.*\\..*).*)'],
};
