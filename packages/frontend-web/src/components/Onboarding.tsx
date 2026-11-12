'use client';

import { useEffect, useState } from 'react';
import { useLocale, useTranslations } from 'next-intl';
import { Link } from '@/i18n/navigation';
import type { Locale } from '@/i18n/locales';
import { authApi } from '@/lib/api/auth';
import type { Certificate, OnboardingOptions, OnboardingState } from '@/lib/api/types';
import { localName } from '@/lib/names';
import { useErrors } from '@/lib/use-errors';
import { useMe } from '@/lib/use-me';
import { ErrorBox } from './AuthCard';
import { Field } from './Field';
import { Mascot } from './Mascot';

const STEPS = ['stepPlace', 'stepGoals', 'stepTarget', 'stepExtra'] as const;
const FIRST_STEP_OF: Record<string, number> = {
  region: 0, district: 0, birth_date: 0, graduation_year: 0,
  goals: 1, primary_goal: 1, dtm_direction_or_subjects: 1,
  test_language: 2, target: 2,
};

interface Form {
  region: string;
  district: string;
  birthDate: string;
  graduationYear: string;
  goals: string[];
  primary: string;
  direction: string;
  language: string;
  score: string;
  grade: string;
  examDate: string;
  certificates: Certificate[];
  parentTelegram: string;
  acquisition: string;
  note: string;
}

const fromState = (s: OnboardingState): Form => ({
  region: s.region ?? '',
  district: s.district ? String(s.district) : '',
  birthDate: s.birth_date ?? '',
  graduationYear: s.graduation_year ? String(s.graduation_year) : '',
  goals: s.goals,
  primary: s.primary_goal ?? '',
  direction: s.dtm_direction ?? '',
  language: s.test_language ?? '',
  score: s.target_score ? String(s.target_score) : '',
  grade: s.target_grade ?? '',
  examDate: s.exam_date ?? '',
  certificates: s.certificates.map((c) => ({ cert_type: c.cert_type, subject: c.subject ?? '', level: c.level, date: c.date })),
  parentTelegram: s.parent_telegram ?? '',
  acquisition: s.acquisition_answer ?? '',
  note: s.acquisition_note ?? '',
});

export function Onboarding() {
  const t = useTranslations('onboarding');
  const tc = useTranslations('common');
  const locale = useLocale() as Locale;
  const errors = useErrors();
  const { me, loading } = useMe();

  const [opts, setOpts] = useState<OnboardingOptions | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [consents, setConsents] = useState<OnboardingState['org_consents']>([]);
  const [step, setStep] = useState(0);
  const [finished, setFinished] = useState(false);
  const [missing, setMissing] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [fieldErr, setFieldErr] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [orgCode, setOrgCode] = useState('');
  const [orgConsent, setOrgConsent] = useState(false);
  const [orgMsg, setOrgMsg] = useState('');

  useEffect(() => {
    if (!me) return;
    let alive = true;
    Promise.all([authApi.onboardingOptions(), authApi.onboarding()])
      .then(([o, s]) => {
        if (!alive) return;
        setOpts(o);
        setForm(fromState(s));
        setConsents(s.org_consents);
      })
      .catch((e) => alive && setError(errors.message(e)));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load once per signed-in user
  }, [me]);

  if (loading || !me || !opts || !form) {
    return error ? <ErrorBox>{error}</ErrorBox> : <p role="status">{tc('loading')}</p>;
  }

  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => (f ? { ...f, [k]: v } : f));
  const region = opts.regions.find((r) => r.code === form.region);
  const selectedGoals = opts.goals.filter((g) => form.goals.includes(g.code));
  const needsDirection = selectedGoals.some((g) => g.requires_direction);

  const ready = [
    !!(form.region && form.district && form.birthDate && form.graduationYear),
    form.goals.length > 0 && !!form.primary && (!needsDirection || !!form.direction),
    !!(form.language && (form.score || form.grade)),
    true,
  ][step];

  const payload = (): Record<string, unknown> => {
    if (step === 0) {
      return { region: form.region, district: Number(form.district), birth_date: form.birthDate, graduation_year: Number(form.graduationYear) };
    }
    if (step === 1) {
      return { goals: form.goals, primary_goal: form.primary, ...(needsDirection ? { dtm_direction: form.direction } : {}) };
    }
    if (step === 2) {
      return {
        test_language: form.language,
        ...(form.score ? { target_score: Number(form.score) } : { target_grade: form.grade }),
        ...(form.examDate ? { exam_date: form.examDate } : {}),
      };
    }
    return {
      certificates: form.certificates.filter((c) => c.cert_type && c.level && c.date).map((c) => ({ ...c, subject: c.subject ?? '' })),
      ...(form.parentTelegram.trim() ? { parent_telegram: form.parentTelegram.trim() } : {}),
      ...(form.acquisition ? { acquisition_answer: form.acquisition, acquisition_note: form.note } : {}),
    };
  };

  const save = async () => {
    setBusy(true);
    setError('');
    setFieldErr({});
    try {
      const s = await authApi.saveOnboarding(payload());
      if (step < STEPS.length - 1) setStep(step + 1);
      else if (s.is_complete) setFinished(true);
      else {
        setMissing(s.missing);
        setStep(Math.min(...s.missing.map((m) => FIRST_STEP_OF[m] ?? 0)));
      }
    } catch (e) {
      setError(errors.message(e));
      setFieldErr(errors.fields(e));
    } finally {
      setBusy(false);
    }
  };

  const join = async () => {
    setError('');
    setOrgMsg('');
    try {
      const r = await authApi.joinOrg(orgCode.trim(), orgConsent);
      setOrgMsg(t('orgJoined', { org: r.organization.name }));
      setConsents((c) => [...c, { organization_id: r.organization.id, organization_name: r.organization.name, consented_at: '' }]);
      setOrgCode('');
      setOrgConsent(false);
    } catch (e) {
      setError(errors.message(e));
    }
  };

  const withdraw = async (id: string) => {
    try {
      await authApi.withdrawOrg(id);
      setConsents((c) => c.filter((x) => x.organization_id !== id));
      setOrgMsg(t('orgWithdrawn'));
    } catch (e) {
      setError(errors.message(e));
    }
  };

  if (finished) {
    return (
      <div className="mx-auto max-w-md text-center">
        <Mascot pose="konfetti" size={180} />
        <h1 className="mt-2 text-2xl">{t('doneTitle')}</h1>
        <p className="mt-2 text-muted">{t('doneText')}</p>
        <Link href="/dashboard" className="btn btn-primary mt-5">
          {t('doneCta')}
        </Link>
      </div>
    );
  }

  const certLabel = (code: string) => (t.has(`cert_${code}` as never) ? t(`cert_${code}` as never) : code);
  const updateCert = (i: number, patch: Partial<Certificate>) =>
    set('certificates', form.certificates.map((c, j) => (j === i ? { ...c, ...patch } : c)));

  return (
    <div className="mx-auto w-full max-w-xl">
      <h1 className="text-2xl">{t('title')}</h1>
      <p className="mt-1 text-muted">{t('intro')}</p>
      <div className="mt-4" role="group" aria-label={t('progress', { current: step + 1, total: STEPS.length })}>
        <ol className="flex gap-1" aria-hidden>
          {STEPS.map((s, i) => (
            <li key={s} className={`h-2 flex-1 rounded-full ${i <= step ? 'bg-main' : 'bg-line'}`} />
          ))}
        </ol>
        <p className="mt-2 text-sm font-bold">
          {t('progress', { current: step + 1, total: STEPS.length })} · {t(STEPS[step])}
        </p>
      </div>

      <form
        className="card mt-4 space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (ready) void save();
        }}
      >
        {error && <ErrorBox>{error}</ErrorBox>}
        {missing.length > 0 && (
          <ErrorBox>
            {t('missingTitle')} {missing.map((m) => t(`missing_${m}` as never)).join(', ')}
          </ErrorBox>
        )}

        {step === 0 && (
          <>
            <Field label={t('region')} error={fieldErr.region}>
              {(p) => (
                <select
                  {...p}
                  className="field"
                  value={form.region}
                  onChange={(e) => setForm({ ...form, region: e.target.value, district: '' })}
                  required
                >
                  <option value="">{t('chooseRegion')}</option>
                  {opts.regions.map((r) => (
                    <option key={r.code} value={r.code}>
                      {localName(r, locale)}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <Field label={t('district')} error={fieldErr.district}>
              {(p) => (
                <select {...p} className="field" value={form.district} onChange={(e) => set('district', e.target.value)} disabled={!region} required>
                  <option value="">{t('chooseDistrict')}</option>
                  {region?.districts.map((d) => (
                    <option key={d.id} value={d.id}>
                      {localName(d, locale)}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <Field label={t('birthDate')} error={fieldErr.birth_date}>
              {(p) => (
                <input {...p} type="date" className="field" value={form.birthDate} onChange={(e) => set('birthDate', e.target.value)} autoComplete="bday" required />
              )}
            </Field>
            <Field label={t('graduationYear')} help={t('graduationYearHelp')} error={fieldErr.graduation_year}>
              {(p) => (
                <input
                  {...p}
                  type="number"
                  inputMode="numeric"
                  className="field"
                  min={opts.graduation_year_range[0]}
                  max={opts.graduation_year_range[1]}
                  value={form.graduationYear}
                  onChange={(e) => set('graduationYear', e.target.value)}
                  required
                />
              )}
            </Field>
          </>
        )}

        {step === 1 && (
          <>
            <fieldset>
              <legend className="font-bold">{t('goals')}</legend>
              <p className="mb-2 text-sm text-muted">{t('goalsHelp')}</p>
              <ul className="space-y-2">
                {opts.goals.map((g) => {
                  const on = form.goals.includes(g.code);
                  return (
                    <li key={g.code} className="flex flex-wrap items-center gap-3 rounded-xl border border-line p-2">
                      <label className="flex min-h-11 flex-1 cursor-pointer items-center gap-3">
                        <input
                          type="checkbox"
                          className="size-5 accent-[var(--c-main)]"
                          checked={on}
                          onChange={(e) => {
                            const goals = e.target.checked ? [...form.goals, g.code] : form.goals.filter((x) => x !== g.code);
                            setForm({ ...form, goals, primary: goals.includes(form.primary) ? form.primary : goals.length === 1 ? goals[0] : '' });
                          }}
                        />
                        {localName(g, locale)}
                      </label>
                      {on && (
                        <label className="flex min-h-11 cursor-pointer items-center gap-2 text-sm font-bold">
                          <input type="radio" name="primary" className="size-5 accent-[var(--c-main)]" checked={form.primary === g.code} onChange={() => set('primary', g.code)} />
                          {t('primary')}
                        </label>
                      )}
                    </li>
                  );
                })}
              </ul>
              {fieldErr.goals && <p role="alert" className="mt-1 text-sm font-semibold text-danger">{fieldErr.goals}</p>}
              {fieldErr.primary_goal && <p role="alert" className="mt-1 text-sm font-semibold text-danger">{fieldErr.primary_goal}</p>}
            </fieldset>
            {needsDirection && (
              <Field label={t('dtmDirection')} error={fieldErr.dtm_direction}>
                {(p) => (
                  <select {...p} className="field" value={form.direction} onChange={(e) => set('direction', e.target.value)} required>
                    <option value="">{tc('choose')}</option>
                    {opts.dtm_directions.map((d) => (
                      <option key={d.code} value={d.code}>
                        {localName(d, locale)}
                      </option>
                    ))}
                  </select>
                )}
              </Field>
            )}
            {needsDirection &&
              opts.dtm_directions.find((d) => d.code === form.direction)?.passing_score &&
              (() => {
                const s = opts.dtm_directions.find((d) => d.code === form.direction)!.passing_score!;
                return <p className="text-sm text-muted">{t('passingScore', { year: s.year, grant: s.grant, contract: s.contract })}</p>;
              })()}
          </>
        )}

        {step === 2 && (
          <>
            <Field label={t('testLanguage')} error={fieldErr.test_language}>
              {(p) => (
                <select {...p} className="field" value={form.language} onChange={(e) => set('language', e.target.value)} required>
                  <option value="">{tc('choose')}</option>
                  {opts.test_languages.map((l) => (
                    <option key={l.code} value={l.code}>
                      {t.has(`lang_${l.code}` as never) ? t(`lang_${l.code}` as never) : l.name}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <fieldset className="space-y-3">
              <legend className="font-bold">{t('targetTitle')}</legend>
              <Field label={t('targetScore', { max: opts.target_score_max })} error={fieldErr.target_score}>
                {(p) => (
                  <input
                    {...p}
                    type="number"
                    inputMode="numeric"
                    className="field"
                    min={1}
                    max={opts.target_score_max}
                    value={form.score}
                    onChange={(e) => setForm({ ...form, score: e.target.value, grade: e.target.value ? '' : form.grade })}
                  />
                )}
              </Field>
              <Field label={t('targetGrade')} error={fieldErr.target_grade}>
                {(p) => (
                  <select {...p} className="field" value={form.grade} onChange={(e) => setForm({ ...form, grade: e.target.value, score: e.target.value ? '' : form.score })}>
                    <option value="">{tc('choose')}</option>
                    {opts.target_grades.map((g) => (
                      <option key={g} value={g}>
                        {g}
                      </option>
                    ))}
                  </select>
                )}
              </Field>
            </fieldset>
            <Field label={t('examDate')} optional error={fieldErr.exam_date}>
              {(p) => <input {...p} type="date" className="field" value={form.examDate} onChange={(e) => set('examDate', e.target.value)} />}
            </Field>
            {opts.exam_calendar.length > 0 && (
              <Field label={t('examCalendar')} optional>
                {(p) => (
                  <select {...p} className="field" value="" onChange={(e) => e.target.value && set('examDate', e.target.value)}>
                    <option value="">{tc('choose')}</option>
                    {opts.exam_calendar.map((ev, i) => (
                      <option key={`${ev.exam_goal}-${ev.date}-${i}`} value={ev.date}>
                        {ev.title} · {ev.date}
                      </option>
                    ))}
                  </select>
                )}
              </Field>
            )}
          </>
        )}

        {step === 3 && (
          <>
            <fieldset className="space-y-3">
              <legend className="font-bold">
                {t('certificates')} <span className="font-normal text-muted">({tc('optional')})</span>
              </legend>
              {form.certificates.map((c, i) => (
                <div key={i} className="grid gap-2 rounded-xl border border-line p-3 sm:grid-cols-2">
                  <Field label={t('certType')}>
                    {(p) => (
                      <select {...p} className="field" value={c.cert_type} onChange={(e) => updateCert(i, { cert_type: e.target.value })}>
                        <option value="">{tc('choose')}</option>
                        {opts.certificate_types.map((ct) => (
                          <option key={ct.code} value={ct.code}>
                            {certLabel(ct.code)}
                          </option>
                        ))}
                      </select>
                    )}
                  </Field>
                  <Field label={t('certLevel')}>
                    {(p) => <input {...p} className="field" value={c.level} onChange={(e) => updateCert(i, { level: e.target.value })} />}
                  </Field>
                  <Field label={t('certDate')}>
                    {(p) => <input {...p} type="date" className="field" value={c.date} onChange={(e) => updateCert(i, { date: e.target.value })} />}
                  </Field>
                  <Field label={t('certSubject')} optional>
                    {(p) => <input {...p} className="field" value={c.subject ?? ''} onChange={(e) => updateCert(i, { subject: e.target.value })} />}
                  </Field>
                  <button type="button" className="btn btn-danger sm:col-span-2" onClick={() => set('certificates', form.certificates.filter((_, j) => j !== i))}>
                    {tc('remove')}
                  </button>
                </div>
              ))}
              {form.certificates.length < 20 && (
                <button type="button" className="btn btn-ghost" onClick={() => set('certificates', [...form.certificates, { cert_type: '', subject: '', level: '', date: '' }])}>
                  + {t('addCertificate')}
                </button>
              )}
              {fieldErr.certificates && <p role="alert" className="text-sm font-semibold text-danger">{fieldErr.certificates}</p>}
            </fieldset>

            <Field label={t('parentTelegram')} optional help={t('parentTelegramHelp')} error={fieldErr.parent_telegram}>
              {(p) => (
                <input {...p} className="field" value={form.parentTelegram} onChange={(e) => set('parentTelegram', e.target.value)} autoCapitalize="off" autoCorrect="off" spellCheck={false} />
              )}
            </Field>

            <fieldset className="space-y-3 rounded-xl border border-line p-3">
              <legend className="px-1 font-bold">
                {t('orgTitle')} <span className="font-normal text-muted">({tc('optional')})</span>
              </legend>
              {consents.map((c) => (
                <p key={c.organization_id} className="flex flex-wrap items-center gap-2 text-sm">
                  {t('orgJoined', { org: c.organization_name })}
                  <button type="button" className="btn btn-ghost text-sm" onClick={() => withdraw(c.organization_id)}>
                    {t('orgWithdraw')}
                  </button>
                </p>
              ))}
              <Field label={t('orgCode')}>
                {(p) => (
                  <input {...p} className="field" value={orgCode} onChange={(e) => setOrgCode(e.target.value)} autoCapitalize="off" autoCorrect="off" spellCheck={false} />
                )}
              </Field>
              <label className="flex cursor-pointer items-start gap-3 text-sm">
                <input type="checkbox" className="mt-1 size-5 shrink-0 accent-[var(--c-main)]" checked={orgConsent} onChange={(e) => setOrgConsent(e.target.checked)} />
                {t('orgConsent')}
              </label>
              <button type="button" className="btn btn-ghost" disabled={!orgCode.trim() || !orgConsent} onClick={join}>
                {t('orgJoin')}
              </button>
              {orgMsg && <p role="status" className="text-sm font-semibold text-main">{orgMsg}</p>}
            </fieldset>

            <Field label={t('acquisition')} optional>
              {(p) => (
                <select {...p} className="field" value={form.acquisition} onChange={(e) => set('acquisition', e.target.value)}>
                  <option value="">{tc('choose')}</option>
                  {opts.acquisition_answers.map((a) => (
                    <option key={a} value={a}>
                      {t.has(`answer_${a}` as never) ? t(`answer_${a}` as never) : a}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            {form.acquisition && (
              <Field label={t('acquisitionNote')} optional>
                {(p) => <input {...p} className="field" value={form.note} onChange={(e) => set('note', e.target.value)} maxLength={200} />}
              </Field>
            )}
          </>
        )}

        <div className="flex gap-3 pt-2">
          {step > 0 && (
            <button type="button" className="btn btn-ghost" onClick={() => setStep(step - 1)}>
              {tc('back')}
            </button>
          )}
          <button className="btn btn-primary flex-1" disabled={busy || !ready}>
            {busy ? t('saving') : step === STEPS.length - 1 ? t('finish') : tc('next')}
          </button>
        </div>
      </form>
    </div>
  );
}
