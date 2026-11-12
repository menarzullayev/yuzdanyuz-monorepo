import { LegalPlaceholder } from '@/components/LegalPlaceholder';
import { pageMetadata } from '@/lib/meta';

export const generateMetadata = (p: { params: Promise<{ locale: string }> }) =>
  pageMetadata(p.params, 'legal', 'termsTitle', '/terms');

export default function Page() {
  return <LegalPlaceholder titleKey="termsTitle" />;
}
