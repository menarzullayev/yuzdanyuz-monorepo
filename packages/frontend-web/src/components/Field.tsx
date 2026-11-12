'use client';

import { useId, useState, type InputHTMLAttributes, type ReactNode } from 'react';
import { useTranslations } from 'next-intl';

interface FieldProps {
  label: string;
  error?: string;
  help?: ReactNode;
  optional?: boolean;
  children: (p: { id: string; 'aria-invalid': boolean; 'aria-describedby': string | undefined }) => ReactNode;
}

/** Label + control + help/error wiring. `children` receives the ids/aria props to spread on the control. */
export function Field({ label, error, help, optional, children }: FieldProps) {
  const id = useId();
  const t = useTranslations('common');
  const described = [help ? `${id}-help` : '', error ? `${id}-err` : ''].filter(Boolean).join(' ') || undefined;
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-sm font-bold">
        {label}
        {optional && <span className="ml-1 font-normal text-muted">({t('optional')})</span>}
      </label>
      {children({ id, 'aria-invalid': !!error, 'aria-describedby': described })}
      {help && (
        <p id={`${id}-help`} className="mt-1 text-sm text-muted">
          {help}
        </p>
      )}
      {error && (
        <p id={`${id}-err`} role="alert" className="mt-1 text-sm font-semibold text-danger">
          {error}
        </p>
      )}
    </div>
  );
}

type PasswordProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & {
  showCapsWarning?: boolean;
  invalid?: boolean;
};

export function PasswordInput({ showCapsWarning = true, invalid, onKeyDown, onKeyUp, onBlur, ...rest }: PasswordProps) {
  const t = useTranslations();
  const [visible, setVisible] = useState(false);
  const [caps, setCaps] = useState(false);
  const track = (e: React.KeyboardEvent<HTMLInputElement>) => setCaps(e.getModifierState('CapsLock'));
  return (
    <div>
      <div className="relative">
        <input
          {...rest}
          type={visible ? 'text' : 'password'}
          className="field pr-20"
          aria-invalid={invalid}
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          onKeyDown={(e) => {
            track(e);
            onKeyDown?.(e);
          }}
          onKeyUp={(e) => {
            track(e);
            onKeyUp?.(e);
          }}
          onBlur={(e) => {
            setCaps(false);
            onBlur?.(e);
          }}
        />
        <button
          type="button"
          className="absolute right-1 top-1/2 min-h-11 min-w-11 -translate-y-1/2 rounded-lg px-2 text-sm font-bold text-main"
          onClick={() => setVisible((v) => !v)}
          aria-pressed={visible}
        >
          {visible ? t('common.hide') : t('common.show')}
        </button>
      </div>
      {showCapsWarning && caps && (
        <p role="status" className="mt-1 text-sm font-semibold text-danger">
          ⇪ {t('login.capsLock')}
        </p>
      )}
    </div>
  );
}
