import { defineConfig, devices } from '@playwright/test';

export const STUDENT_STATE = 'e2e/.auth/student.json';

// Browser end-to-end tests (audit H10) and accessibility checks (audit I01).
// They need the whole stack: Django on :8013 with `manage.py e2e_seed`, a Celery worker,
// and this app on E2E_BASE_URL (next start). CI does that in the `e2e` job.
export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.e2e.ts',
  timeout: 120_000,
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:3107',
    trace: 'retain-on-failure',
    locale: 'uz-UZ',
  },
  projects: [
    // Signs in once through the real login form; the API allows 5 logins a minute per IP.
    { name: 'setup', testMatch: /auth\.setup\.ts/ },
    {
      name: 'chromium',
      dependencies: ['setup'],
      use: { ...devices['Desktop Chrome'], storageState: STUDENT_STATE },
    },
  ],
});
