import type { ExamQuestion, QuestionType, SavedAnswer, Selected } from './api/exams';

/** Option ids chosen for a question, whatever shape the server stored them in. */
export function selectedIds(selected: Selected | undefined | null): number[] {
  if (!selected) return [];
  if (Array.isArray(selected)) return selected.filter((n) => Number.isInteger(n));
  return Number.isInteger(selected.id) ? [selected.id] : [];
}

/** New selection after clicking an option: SC replaces, MC toggles (kept sorted). */
export function toggle(type: QuestionType, current: number[], optionId: number): number[] {
  if (type !== 'MC') return [optionId];
  const next = current.includes(optionId)
    ? current.filter((id) => id !== optionId)
    : [...current, optionId];
  return next.sort((a, b) => a - b);
}

/**
 * The body the backend expects: `{id}` for single choice, a list for multiple choice.
 * An emptied MC question is sent as `[]` so the stored answer is cleared too.
 */
export function toSelected(type: QuestionType, ids: number[]): Selected | null {
  if (type === 'MC') return ids;
  return ids.length ? { id: ids[0] } : null;
}

/** Only these types can be answered with the option buttons this page draws. */
export function isSupported(type: QuestionType): boolean {
  return type === 'SC' || type === 'MC';
}

export function answersByQuestion(answers: SavedAnswer[]): Record<string, number[]> {
  const out: Record<string, number[]> = {};
  for (const a of answers) out[a.question_version] = selectedIds(a.selected);
  return out;
}

export function unansweredCount(
  questions: ExamQuestion[],
  chosen: Record<string, number[]>,
): number {
  return questions.filter((q) => !(chosen[q.question.id]?.length > 0)).length;
}

/** 3725 → "1:02:05", 125 → "02:05"; never negative. */
export function formatClock(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${pad(m)}:${pad(sec)}`;
}

/**
 * Seconds left according to the server, corrected for the time since the response
 * arrived. The browser clock is never trusted for the deadline itself.
 */
export function secondsLeft(serverSecondsLeft: number, receivedAtMs: number, nowMs: number): number {
  return Math.max(0, serverSecondsLeft - Math.floor((nowMs - receivedAtMs) / 1000));
}

/** A mock can be started now if its window is open. */
export function isOpen(scheduledAt: string, closesAt: string, nowMs: number): boolean {
  return Date.parse(scheduledAt) <= nowMs && nowMs <= Date.parse(closesAt);
}
