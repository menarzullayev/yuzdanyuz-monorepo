"""OpenAPI schema gate (audit D04): the schema must build and validate, and its error and
warning counts may only go down.

drf-spectacular reports "Errors: N" for every view it cannot describe (mostly plain
APIViews without extend_schema). Documenting them all is ongoing work; this ratchet makes
sure no new undocumented endpoint slips in and that progress is locked in.

Usage (from packages/backend):
    python scripts/check_api_schema.py            # CI: fail if counts grew
    python scripts/check_api_schema.py --update   # after documenting views: lower the baseline
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
BASELINE = BASE / 'docs' / 'api' / 'schema-baseline.json'
_COUNT = re.compile(r'^(Errors|Warnings):\s+(\d+)', re.M)


def parse_counts(output: str) -> dict[str, int]:
    """{'errors': N, 'warnings': M} from spectacular's summary (0 when a line is absent)."""
    found = {k.lower(): int(v) for k, v in _COUNT.findall(output)}
    return {'errors': found.get('errors', 0), 'warnings': found.get('warnings', 0)}


def compare(current: dict[str, int], baseline: dict[str, int]) -> list[str]:
    """Problems when any count is above the baseline."""
    return [
        f'{k}: {current[k]} > baseline {baseline.get(k, 0)}'
        for k in ('errors', 'warnings')
        if current[k] > baseline.get(k, 0)
    ]


def run_spectacular() -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                sys.executable,
                'manage.py',
                'spectacular',
                '--validate',
                '--file',
                f'{tmp}/schema.yml',
            ],
            cwd=BASE,
            capture_output=True,
            text=True,
            check=False,
        )
    return proc.returncode, proc.stdout + proc.stderr


def main(argv: list[str]) -> int:
    code, output = run_spectacular()
    if code != 0:
        print(output[-4000:])
        print('FAIL: the OpenAPI schema does not build or validate')
        return 1
    current = parse_counts(output)
    if '--update' in argv:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(json.dumps(current, indent=2) + '\n', encoding='utf-8')
        print(f'baseline written: {current}')
        return 0
    baseline = json.loads(BASELINE.read_text(encoding='utf-8'))
    problems = compare(current, baseline)
    if problems:
        print('FAIL: ' + '; '.join(problems))
        print('Document the new endpoint with drf_spectacular extend_schema.')
        return 1
    lower = [k for k in ('errors', 'warnings') if current[k] < baseline.get(k, 0)]
    print(f'ok: {current} (baseline {baseline})')
    if lower:
        print('Counts went down: run with --update and commit docs/api/schema-baseline.json.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
