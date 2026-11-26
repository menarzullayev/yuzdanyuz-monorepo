import { ExamRunner } from '@/components/ExamRunner';
import { pageMetadata } from '@/lib/meta';

type Params = Promise<{ locale: string; mockId: string }>;

export const generateMetadata = async (p: { params: Params }) => {
  const { mockId } = await p.params;
  return pageMetadata(p.params, 'exam', 'title', `/exams/${mockId}`);
};

export default async function ExamRunPage({ params }: { params: Params }) {
  const { mockId } = await params;
  return <ExamRunner mockId={mockId} />;
}
