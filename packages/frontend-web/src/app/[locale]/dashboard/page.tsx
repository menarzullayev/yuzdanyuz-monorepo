import { Dashboard } from '@/components/Dashboard';
import { pageMetadata } from '@/lib/meta';

export const generateMetadata = (p: { params: Promise<{ locale: string }> }) =>
  pageMetadata(p.params, 'dashboard', 'title', '/dashboard');

export default function DashboardPage() {
  return <Dashboard />;
}
