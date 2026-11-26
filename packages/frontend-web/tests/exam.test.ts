import { describe, expect, it } from 'vitest';
import type { ExamQuestion } from '../src/lib/api/exams';
import {
  answersByQuestion,
  formatClock,
  isOpen,
  isSupported,
  secondsLeft,
  selectedIds,
  toSelected,
  toggle,
  unansweredCount,
} from '../src/lib/exam';

const q = (id: string): ExamQuestion => ({
  order: 1,
  points: 1,
  question: { id, type: 'SC', content: { text: id }, options: [] },
});

describe('selection', () => {
  it('reads both stored shapes', () => {
    expect(selectedIds({ id: 3 })).toEqual([3]);
    expect(selectedIds([2, 1])).toEqual([2, 1]);
    expect(selectedIds(null)).toEqual([]);
    expect(selectedIds(undefined)).toEqual([]);
  });

  it('ignores malformed ids', () => {
    expect(selectedIds({ id: 'x' as unknown as number })).toEqual([]);
    expect(selectedIds([1, 'y' as unknown as number])).toEqual([1]);
  });

  it('single choice replaces, multiple choice toggles and sorts', () => {
    expect(toggle('SC', [1], 2)).toEqual([2]);
    expect(toggle('SC', [], 1)).toEqual([1]);
    expect(toggle('MC', [3], 1)).toEqual([1, 3]);
    expect(toggle('MC', [1, 3], 3)).toEqual([1]);
  });

  it('builds the body the backend expects', () => {
    expect(toSelected('SC', [4])).toEqual({ id: 4 });
    expect(toSelected('MC', [1, 4])).toEqual([1, 4]);
    expect(toSelected('MC', [])).toEqual([]); // clears the stored answer
    expect(toSelected('SC', [])).toBeNull();
  });

  it('only option-based types are answerable here', () => {
    expect(isSupported('SC')).toBe(true);
    expect(isSupported('MC')).toBe(true);
    expect(isSupported('OE')).toBe(false);
    expect(isSupported('MT')).toBe(false);
  });

  it('maps saved answers and counts the unanswered', () => {
    const chosen = answersByQuestion([
      { id: 'a', question_version: 'q1', selected: { id: 2 }, time_spent_seconds: 0, answered_at: '' },
      { id: 'b', question_version: 'q2', selected: [], time_spent_seconds: 0, answered_at: '' },
    ]);
    expect(chosen).toEqual({ q1: [2], q2: [] });
    expect(unansweredCount([q('q1'), q('q2'), q('q3')], chosen)).toBe(2);
  });
});

describe('clock', () => {
  it('formats minutes and hours', () => {
    expect(formatClock(125)).toBe('02:05');
    expect(formatClock(3725)).toBe('1:02:05');
    expect(formatClock(0)).toBe('00:00');
    expect(formatClock(-5)).toBe('00:00');
  });

  it('counts down from the server value, never below zero', () => {
    expect(secondsLeft(600, 1_000, 1_000)).toBe(600);
    expect(secondsLeft(600, 1_000, 61_000)).toBe(540);
    expect(secondsLeft(10, 0, 60_000)).toBe(0);
  });

  it('knows whether a mock window is open', () => {
    const now = Date.parse('2026-11-26T10:00:00Z');
    expect(isOpen('2026-11-26T09:00:00Z', '2026-11-26T11:00:00Z', now)).toBe(true);
    expect(isOpen('2026-11-26T10:30:00Z', '2026-11-26T11:00:00Z', now)).toBe(false);
    expect(isOpen('2026-11-26T08:00:00Z', '2026-11-26T09:00:00Z', now)).toBe(false);
  });
});
