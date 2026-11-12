import type { ReactNode } from 'react';
import { Mascot, type Pose } from './Mascot';

/** Centered card with the mascot on top; shared by every auth page. */
export function AuthCard({
  pose = 'salom',
  title,
  subtitle,
  children,
}: {
  pose?: Pose;
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <div className="mx-auto w-full max-w-md">
      <Mascot pose={pose} size={120} />
      <div className="card mt-2">
        <h1 className="text-center text-2xl">{title}</h1>
        {subtitle && <p className="mt-2 text-center text-muted">{subtitle}</p>}
        <div className="mt-5 space-y-4">{children}</div>
      </div>
    </div>
  );
}

export function ErrorBox({ children }: { children: ReactNode }) {
  return (
    <div role="alert" className="rounded-xl bg-danger-bg p-3 text-sm font-semibold text-danger">
      {children}
    </div>
  );
}
