import { ExamList } from '@/components/ExamList';
import { pageMetadata } from '@/lib/meta';

export const generateMetadata = (p: { params: Promise<{ locale: string }> }) =>
  pageMetadata(p.params, 'exam', 'title', '/exams');

export default function ExamsPage() {
  return <ExamList />;
}
