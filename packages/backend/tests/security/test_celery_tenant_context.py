"""ISSUE-X05 — Celery tasks tenant context audit.

Maqsad: har Celery `@shared_task` funksiyasi ishlash boshida `tenant_context()`,
`unscoped_context()` yoki tenant-aware helper chaqirishi shart. Aks holda:

  - Worker'da `get_current_org()` `None` qaytaradi
  - TenantManager fail-closed → bo'sh queryset
  - Silent data inaccessibility (eng yomon: developer'lar haftalab fail-silent task'larni
    bilmasdan production'da ishlatishadi)

Bu fayl 2 tartibni implement qiladi:
  1. **Discovery test** — barcha tasks.py fayllarni walk qilib, `@shared_task` yoki
     `@app.task`-marked funksiyalarni topadi va manual allow-list bilan solishtiradi.
  2. **Explicit allow-list** — har task uchun strategy belgilangan (`unscoped`, `tenant`,
     `stateless`). Yangi task qo'shilsa, bu fayl yangilanmaguncha test fail bo'ladi.

Allow-list — manual yangilash kerak. Bu intentional friction: developer'lar
yangi task qo'shganda tenant context'ni o'ylab ko'rishga majbur qiladi.
"""

import ast
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
TASK_FILES = [
    'apps/intelligence/tasks.py',
    'apps/analytics/tasks.py',
    'apps/commerce/tasks.py',
    'apps/exams/tasks.py',
    'apps/engagement/tasks.py',
    'apps/engagement/notifications_service.py',
]

# ── Allow-list ──────────────────────────────────────────────────────────────
#
# Har task uchun tenant context strategy:
#   - 'unscoped' — `with unscoped_context()` ishlatadi (cross-tenant scan)
#   - 'tenant'   — task `org_id` arg oladi va `tenant_context(org)` o'rab oladi
#   - 'stateless' — DB query qilmaydi yoki faqat user-scoped (org FK yo'q)
#   - 'inherits' — Celery chain'da parent task tomonidan o'rnatilgan context'ni
#                  meros qiladi (eager mode bilan ishonchli emas — eslatma yoz)
#
# Yangi task qo'shilsa, bu dict'ga entry qo'shing.
EXPECTED_TASKS = {
    # apps/engagement/tasks.py
    'archive_leaderboards': 'unscoped',  # global/region/tenant scan
    'check_broken_streaks_task': 'stateless',  # UserStreak user-scoped
    'leagues_weekly_recalc_task': 'unscoped',  # cross-tenant leaderboard scan
    # apps/engagement/notifications_service.py
    'fan_out_notification': 'stateless',  # Notification user-scoped
    # apps/exams/tasks.py
    'finalize_attempt_score': 'tenant',  # attempt.organization context
    'publish_scheduled_mocks': 'unscoped',  # cross-tenant beat task
    'quarantine_check': 'tenant',  # question_version.organization
    # apps/commerce/tasks.py
    'auto_renew_subscriptions': 'unscoped',  # cross-tenant scan
    # apps/intelligence/tasks.py
    'generate_ai_tutor_feedback': 'tenant',  # attempt-scoped
    'evaluate_openended_submission': 'tenant',
    # apps/analytics/tasks.py
    'generate_export': 'tenant',  # report.organization
}


def _collect_task_names(path: Path) -> list[str]:
    """AST scan: return function names decorated with @shared_task or @app.task."""
    src = path.read_text()
    tree = ast.parse(src)
    names = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            # @shared_task or @shared_task(name='...')
            name_node = dec.func if isinstance(dec, ast.Call) else dec
            attr = getattr(name_node, 'attr', None) or getattr(name_node, 'id', None)
            if attr in ('shared_task', 'task'):
                names.append(node.name)
                break
    return names


@pytest.mark.security
class TestCeleryTaskTenantAudit:
    def test_all_known_task_files_exist(self):
        for rel in TASK_FILES:
            assert (BACKEND_ROOT / rel).exists(), f'Missing task file: {rel}'

    def test_every_celery_task_has_strategy_entry(self):
        """Yangi task qo'shilsa, EXPECTED_TASKS dict'ga strategy entry kerak."""
        discovered = set()
        for rel in TASK_FILES:
            path = BACKEND_ROOT / rel
            for name in _collect_task_names(path):
                discovered.add(name)

        missing = discovered - set(EXPECTED_TASKS.keys())
        unexpected = set(EXPECTED_TASKS.keys()) - discovered

        # Allow-list'da bor, lekin code'da yo'q (rename/o'chirish — entry'ni yangilang)
        assert not unexpected, (
            f'Allow-list contains stale entries (task no longer exists): {sorted(unexpected)}. '
            f'Remove from EXPECTED_TASKS in {__file__}'
        )

        # Code'da bor, allow-list'da yo'q (yangi task — strategy belgilang)
        assert not missing, (
            f'New Celery tasks without tenant context strategy: {sorted(missing)}. '
            f'Add entry to EXPECTED_TASKS in {__file__} with one of: '
            f"'unscoped', 'tenant', 'stateless'"
        )

    def test_strategy_values_are_valid(self):
        valid = {'unscoped', 'tenant', 'stateless', 'inherits'}
        for task_name, strategy in EXPECTED_TASKS.items():
            assert strategy in valid, (
                f'Task {task_name} has invalid strategy: {strategy}. Use one of {sorted(valid)}'
            )
