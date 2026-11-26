"""Audit D04: the OpenAPI ratchet parses spectacular's summary and only lets counts fall."""

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / 'scripts' / 'check_api_schema.py'
_spec = importlib.util.spec_from_file_location('check_api_schema', _PATH)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

SAMPLE = """
/app/apps/x.py:1: Error [V]: unable to guess serializer.

Schema generation summary:
Warnings: 14 (12 unique)
Errors:   843 (126 unique)
"""


def test_parses_summary():
    assert gate.parse_counts(SAMPLE) == {'errors': 843, 'warnings': 14}


def test_missing_lines_count_as_zero():
    assert gate.parse_counts('all good') == {'errors': 0, 'warnings': 0}


def test_equal_or_lower_passes():
    assert gate.compare({'errors': 843, 'warnings': 14}, {'errors': 843, 'warnings': 14}) == []
    assert gate.compare({'errors': 10, 'warnings': 0}, {'errors': 843, 'warnings': 14}) == []


@pytest.mark.parametrize(
    'current', [{'errors': 844, 'warnings': 14}, {'errors': 843, 'warnings': 15}]
)
def test_growth_fails(current):
    problems = gate.compare(current, {'errors': 843, 'warnings': 14})
    assert len(problems) == 1 and 'baseline' in problems[0]


def test_committed_baseline_is_well_formed():
    import json

    data = json.loads(gate.BASELINE.read_text(encoding='utf-8'))
    assert set(data) == {'errors', 'warnings'}
    assert all(isinstance(v, int) and v >= 0 for v in data.values())
