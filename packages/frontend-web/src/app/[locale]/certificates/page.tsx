import { CertificateList } from '@/components/CertificateList';
import { pageMetadata } from '@/lib/meta';

export const generateMetadata = (p: { params: Promise<{ locale: string }> }) =>
  pageMetadata(p.params, 'exam', 'certificates', '/certificates');

export default function CertificatesPage() {
  return <CertificateList />;
}
