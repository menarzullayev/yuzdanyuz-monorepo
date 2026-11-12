import { Suspense } from 'react';
import { LoginFlow } from '@/components/LoginFlow';
import { pageMetadata } from '@/lib/meta';

export const generateMetadata = (p: { params: Promise<{ locale: string }> }) =>
  pageMetadata(p.params, 'login', 'submit', '/login');

export default function LoginPage() {
  return (
    <Suspense>
      <LoginFlow />
    </Suspense>
  );
}
