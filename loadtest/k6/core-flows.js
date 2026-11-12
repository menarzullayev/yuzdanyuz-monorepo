// Core exam flow under load (audit F20): login -> list mocks -> start attempt ->
// answer every question -> submit. Peak x2 target with pass/fail thresholds.
//
// Run against loadtest/compose.yml (see docs/operations/load-testing.md):
//   docker run --rm --network yuzdanyuz-loadtest_default -v "$PWD/loadtest:/loadtest" \
//     -e BASE_URL=http://web:8000 -e LOADTEST_PASSWORD=... grafana/k6:1.8.2 run /loadtest/k6/core-flows.js
//
// Each VU is its own user (lt-user-<VU>) with its own client IP (X-Forwarded-For from
// 198.18.0.0/15, believed because the k6 container is in TRUSTED_PROXY_CIDRS), the
// way real students arrive through the ingress. A user may attempt a mock once, so
// iteration n uses mock n of the seeded list.
import http from 'k6/http';
import { check, fail, sleep } from 'k6';
import { Trend } from 'k6/metrics';

const BASE = __ENV.BASE_URL || 'http://web:8000';
const PASSWORD = __ENV.LOADTEST_PASSWORD;
// Peak = 200 concurrent exam takers (assumption, see the report); target = peak x2.
const PEAK = Number(__ENV.PEAK_VUS || 200);
const HOLD = __ENV.HOLD || '3m';
const MOCKS = JSON.parse(open('/loadtest/out/mocks.json'));

const flowSeconds = new Trend('exam_flow_seconds', true);

export const options = {
  scenarios: {
    exam_takers: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '1m', target: PEAK },
        { duration: '2m', target: PEAK },
        { duration: '1m', target: PEAK * 2 },
        { duration: HOLD, target: PEAK * 2 },
        { duration: '30s', target: 0 },
      ],
      gracefulRampDown: '60s',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    checks: ['rate>0.99'],
    http_req_duration: ['p(95)<800', 'p(99)<2000'],
    'http_req_duration{step:login}': ['p(95)<1500'],
    'http_req_duration{step:list}': ['p(95)<500'],
    'http_req_duration{step:start}': ['p(95)<1000'],
    'http_req_duration{step:answer}': ['p(95)<500'],
    'http_req_duration{step:submit}': ['p(95)<1000'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

const HOST = BASE.replace(/^https?:\/\//, '');
// Django checks Origin against the Host; prod settings see https via X-Forwarded-Proto.
const ORIGIN = `https://${HOST}`;

// Django accepts any 32-char alphanumeric CSRF secret as long as the csrftoken cookie
// and the X-CSRFToken header carry the same value (the web app does the same).
const CSRF_FALLBACK = 'k6LoadTestCsrfToken0123456789abc';

let session = null; // { access, device, csrf, at }

// IP_OFFSET gives repeated runs fresh client IPs (the login limit is 5/min per IP).
function clientIp() {
  const n = __VU + Number(__ENV.IP_OFFSET || 0);
  return `198.18.${Math.floor(n / 250) % 256}.${(n % 250) + 1}`;
}

function headers(extra) {
  const h = {
    'X-Forwarded-Proto': 'https',
    'X-Forwarded-For': clientIp(),
    'User-Agent': 'k6-loadtest',
    Accept: 'application/json',
  };
  return Object.assign(h, extra || {});
}

function login() {
  const user = `lt-user-${String(__VU).padStart(4, '0')}`;
  const res = http.post(
    `${BASE}/api/auth/login/`,
    JSON.stringify({ username: user, password: PASSWORD, remember: false }),
    { headers: headers({ 'Content-Type': 'application/json' }), tags: { step: 'login' } },
  );
  if (!check(res, { 'login 200': (r) => r.status === 200 })) {
    fail(`login ${user}: ${res.status} ${res.body && res.body.slice(0, 200)}`);
  }
  // Secure cookies are not replayed over plain http by the jar; send them by hand.
  const access = res.cookies.access_token && res.cookies.access_token[0].value;
  const device = res.cookies.device_id && res.cookies.device_id[0].value;
  session = { access, device, csrf: CSRF_FALLBACK, at: Date.now() };
}

function authed(method, path, body, step) {
  const cookie = `access_token=${session.access}; device_id=${session.device}; csrftoken=${session.csrf}`;
  const h = headers({
    Cookie: cookie,
    'X-CSRFToken': session.csrf,
    Origin: ORIGIN,
    Referer: `${ORIGIN}/`,
    'Content-Type': 'application/json',
  });
  const res = http.request(method, `${BASE}${path}`, body ? JSON.stringify(body) : null, {
    headers: h,
    tags: { step },
  });
  if (__ENV.LOG_FAILURES && res.status >= 400 && Math.random() < 0.05) {
    console.warn(`${step} ${res.status} ${String(res.body).slice(0, 160)}`);
  }
  return res;
}

export default function () {
  // The access token lives 15 minutes; log in again before it runs out.
  if (!session || Date.now() - session.at > 12 * 60 * 1000) {
    login();
  }
  const started = Date.now();

  const list = authed('GET', '/api/v1/exams/mocks/', null, 'list');
  check(list, { 'list 200': (r) => r.status === 200 });
  if (list.cookies.csrftoken && list.cookies.csrftoken.length) {
    session.csrf = list.cookies.csrftoken[0].value; // a rotated server-issued token wins
  }

  const mockId = MOCKS[__ITER % MOCKS.length];
  const start = authed('POST', `/api/v1/exams/mocks/${mockId}/start/`, null, 'start');
  if (!check(start, { 'start 201': (r) => r.status === 201 })) {
    // A student who sees an error waits before trying again; a 1 s retry loop only
    // measures the per-user rate limit (and escalates its block).
    sleep(5 + Math.random() * 5);
    return;
  }
  const payload = start.json();
  const attemptId = payload.attempt.id;
  for (const item of payload.exam.questions) {
    sleep(1 + Math.random() * 2); // reading the question
    const option = item.question.options[Math.floor(Math.random() * item.question.options.length)];
    const answer = authed(
      'POST',
      `/api/v1/exams/attempts/${attemptId}/answer/`,
      { question_version: item.question.id, selected: { id: option.id }, time_spent_seconds: 2 },
      'answer',
    );
    check(answer, { 'answer 201': (r) => r.status === 201 });
  }

  const submit = authed('POST', `/api/v1/exams/attempts/${attemptId}/submit/`, null, 'submit');
  check(submit, { 'submit 200': (r) => r.status === 200 });
  flowSeconds.add((Date.now() - started) / 1000);
  sleep(2 + Math.random() * 3);
}

export function handleSummary(data) {
  const m = data.metrics;
  const pick = (name) => (m[name] ? m[name].values : null);
  const steps = {};
  for (const step of ['login', 'list', 'start', 'answer', 'submit']) {
    steps[step] = pick(`http_req_duration{step:${step}}`);
  }
  const brief = {
    vus_max: pick('vus_max'),
    iterations: pick('iterations'),
    http_reqs: pick('http_reqs'),
    http_req_failed: pick('http_req_failed'),
    checks: pick('checks'),
    http_req_duration: pick('http_req_duration'),
    exam_flow_seconds: pick('exam_flow_seconds'),
    steps,
    thresholds: Object.fromEntries(
      Object.entries(m)
        .filter(([, v]) => v.thresholds)
        .map(([k, v]) => [k, Object.fromEntries(Object.entries(v.thresholds).map(([t, r]) => [t, !r.ok ? 'FAIL' : 'ok']))]),
    ),
  };
  return {
    '/loadtest/out/summary.json': JSON.stringify(brief, null, 2),
    stdout: `\n${JSON.stringify(brief, null, 2)}\n`,
  };
}
