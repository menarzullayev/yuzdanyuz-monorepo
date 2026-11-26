import { ExamResult } from '@/components/ExamResult';
import { pageMetadata } from '@/lib/meta';

type Params = Promise<{ locale: string; attemptId: string }>;

export const generateMetadata = async (p: { params: Params }) => {
  const { attemptId } = await p.params;
  return pageMetadata(p.params, 'exam', 'resultTitle', `/exams/result/${attemptId}`);
};

export default async function ExamResultPage({ params }: { params: Params }) {
  const { attemptId } = await params;
  return <ExamResult attemptId={attemptId} />;
}
