import { RecoverConfirmForm } from '@/components/RecoverConfirmForm';
import { pageMetadata } from '@/lib/meta';

export const generateMetadata = async (p: { params: Promise<{ locale: string }> }) => ({
  ...(await pageMetadata(p.params, 'recoverConfirm', 'title', '/recover/confirm')),
  // The page URL carries a secret in the fragment; keep it out of search engines.
  robots: { index: false, follow: false },
});

export default function RecoverConfirmPage() {
  return <RecoverConfirmForm />;
}
