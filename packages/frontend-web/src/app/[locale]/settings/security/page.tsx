import { SecuritySettings } from '@/components/SecuritySettings';
import { pageMetadata } from '@/lib/meta';

export const generateMetadata = (p: { params: Promise<{ locale: string }> }) =>
  pageMetadata(p.params, 'security', 'title', '/settings/security');

export default function SecurityPage() {
  return <SecuritySettings />;
}
