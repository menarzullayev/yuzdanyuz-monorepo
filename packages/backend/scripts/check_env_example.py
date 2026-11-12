#!/usr/bin/env python3
"""Fail when the backend reads an env key that `.env.example` does not document.

Scans every non-test Python file under packages/backend for literal keys passed to
`os.getenv(...)`, `os.environ.get(...)`, `os.environ[...]` and
`os.environ.setdefault(...)`, and compares them with the keys in `.env.example`
(`KEY=value` or a commented `# KEY=value` for optional ones). It also reports keys
in the example that nothing reads any more.

Usage: python scripts/check_env_example.py [--root packages/backend] [--list]
Exit code 1 when keys are missing or unused. CI runs it through
tests/unit/test_env_example.py.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

SKIP_DIRS = {'tests', 'migrations', 'node_modules', '.venv', 'venv', '__pycache__', 'staticfiles'}
EXAMPLE_KEY = re.compile(r'^\s*#?\s*([A-Z][A-Z0-9_]*)=', re.MULTILINE)


def _is_os_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == 'environ'
        and isinstance(node.value, ast.Name)
        and node.value.id == 'os'
    )


def _literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def keys_in_source(source: str) -> set[str]:
    keys: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and node.args:
            func = node.func
            is_getenv = (
                isinstance(func, ast.Attribute)
                and func.attr == 'getenv'
                and isinstance(func.value, ast.Name)
                and func.value.id == 'os'
            )
            is_environ_method = (
                isinstance(func, ast.Attribute)
                and func.attr in ('get', 'setdefault')
                and _is_os_environ(func.value)
            )
            if is_getenv or is_environ_method:
                key = _literal(node.args[0])
                if key:
                    keys.add(key)
        elif isinstance(node, ast.Subscript) and _is_os_environ(node.value):
            key = _literal(node.slice)
            if key:
                keys.add(key)
    return keys


def keys_read(root: Path) -> set[str]:
    keys: set[str] = set()
    for path in root.rglob('*.py'):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        if path.name.startswith('test_') or path.name == 'conftest.py':
            continue
        keys |= keys_in_source(path.read_text(encoding='utf-8'))
    return keys


def keys_documented(example: Path) -> set[str]:
    if not example.exists():
        return set()
    return set(EXAMPLE_KEY.findall(example.read_text(encoding='utf-8')))


def check(root: Path, example: Path) -> tuple[list[str], list[str]]:
    read = keys_read(root)
    documented = keys_documented(example)
    return sorted(read - documented), sorted(documented - read)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--example', type=Path, default=None)
    parser.add_argument('--list', action='store_true', help='print every key read and exit')
    args = parser.parse_args()
    example = args.example or args.root / '.env.example'
    if args.list:
        print('\n'.join(sorted(keys_read(args.root))))
        return 0
    missing, unused = check(args.root, example)
    for key in missing:
        print(f'MISSING from {example}: {key}')
    for key in unused:
        print(f'UNUSED in {example} (nothing reads it): {key}')
    if missing or unused:
        return 1
    print(f'ok: {example} documents all {len(keys_read(args.root))} env keys')
    return 0


if __name__ == '__main__':
    sys.exit(main())
