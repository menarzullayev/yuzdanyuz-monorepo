// ESLint 9 flat config — Next.js 16 native (no FlatCompat needed)
import nextConfig from 'eslint-config-next';
import nextCoreWebVitals from 'eslint-config-next/core-web-vitals';
import nextTypeScript from 'eslint-config-next/typescript';
import prettierConfig from 'eslint-config-prettier';

const config = [
  ...nextConfig,
  ...nextCoreWebVitals,
  ...nextTypeScript,
  prettierConfig,
  {
    ignores: ['.next/**', 'node_modules/**', 'public/**', '*.config.{js,mjs}'],
    rules: {
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
    },
  },
];

export default config;
