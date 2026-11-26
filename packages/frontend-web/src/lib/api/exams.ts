import { api } from './client';

const BASE = '/api/exams';

export interface MockExam {
  id: string;
  title: string;
  description: string;
  duration_minutes: number;
  scheduled_at: string;
  closes_at: string;
  status: string;
  is_public: boolean;
  question_count: number;
}

export interface ExamOption {
  id: number;
  text: string;
}

/** SC = single choice, MC = multiple choice; other types are not taken in the browser yet. */
export type QuestionType = 'SC' | 'MC' | 'MT' | 'OR' | 'FB' | 'OE' | 'FU';

export interface ExamQuestion {
  order: number;
  points: number;
  question: {
    id: string;
    type: QuestionType;
    content: { text?: string; [key: string]: unknown };
    options: ExamOption[];
  };
}

export type AttemptStatus = 'in_progress' | 'submitted' | 'cancelled' | 'disputed' | 'expired';

export interface Attempt {
  id: string;
  exam: string;
  exam_title: string;
  duration_minutes: number;
  status: AttemptStatus;
  started_at: string;
  submitted_at: string | null;
  score: string | null;
  correct_count: number | null;
  total_points: number | null;
  strikes: number;
  cancel_reason: string | null;
  deadline: string;
  seconds_left: number;
  onboarding_required?: boolean;
  missing?: string[];
}

/** What the server stored for one question: `{id}` for SC, `[id, ...]` for MC. */
export type Selected = { id: number } | number[];

export interface SavedAnswer {
  id: string;
  question_version: string;
  selected: Selected;
  time_spent_seconds: number;
  answered_at: string;
}

export interface StartPayload {
  attempt: Attempt;
  exam: MockExam & { questions: ExamQuestion[] };
  answers: SavedAnswer[];
}

export interface Certificate {
  id: string;
  number: string;
  sitting: string;
  scaled_score: string;
  grade: string;
  issued_at: string;
  valid_until: string | null;
  revoked_at: string | null;
  status: string;
}

interface Page<T> {
  count: number;
  results: T[];
}

export type AntiCheatEvent = 'tab_switch' | 'window_blur' | 'fullscreen_exit' | 'copy_attempt';

export const examsApi = {
  mocks: async (): Promise<MockExam[]> => {
    const page = await api<Page<MockExam> | MockExam[]>('/mocks/', { base: BASE });
    return Array.isArray(page) ? page : page.results;
  },
  start: (mockId: string) =>
    api<StartPayload>(`/mocks/${mockId}/start/`, { base: BASE, method: 'POST' }),
  attempt: (attemptId: string) => api<Attempt>(`/attempts/${attemptId}/`, { base: BASE }),
  answer: (attemptId: string, questionVersion: string, selected: Selected, timeSpent: number) =>
    api<SavedAnswer>(`/attempts/${attemptId}/answer/`, {
      base: BASE,
      method: 'POST',
      body: { question_version: questionVersion, selected, time_spent_seconds: timeSpent },
    }),
  submit: (attemptId: string) =>
    api<Attempt>(`/attempts/${attemptId}/submit/`, { base: BASE, method: 'POST' }),
  antiCheat: (attemptId: string, eventType: AntiCheatEvent, metadata: Record<string, unknown> = {}) =>
    api<Attempt>(`/attempts/${attemptId}/anticheat/`, {
      base: BASE,
      method: 'POST',
      body: { event_type: eventType, metadata },
    }),
  certificates: () => api<Certificate[]>('/certificates/mine/', { base: BASE }),
};
