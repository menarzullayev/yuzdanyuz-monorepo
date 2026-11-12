import { RecoverForm } from '@/components/RecoverForm';
import { pageMetadata } from '@/lib/meta';

export const generateMetadata = (p: { params: Promise<{ locale: string }> }) =>
  pageMetadata(p.params, 'recover', 'title', '/recover');

export default function RecoverPage() {
  return <RecoverForm />;
}
