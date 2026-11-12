import { HashRedirect } from '@/components/HashRedirect';

// The backend bot links to /reset-password#token=...; the UI lives at /recover/confirm.
export default function ResetPasswordPage() {
  return <HashRedirect to="/recover/confirm" />;
}
