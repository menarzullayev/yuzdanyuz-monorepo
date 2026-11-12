import { Onboarding } from '@/components/Onboarding';
import { pageMetadata } from '@/lib/meta';

export const generateMetadata = (p: { params: Promise<{ locale: string }> }) =>
  pageMetadata(p.params, 'onboarding', 'title', '/onboarding');

export default function OnboardingPage() {
  return <Onboarding />;
}
