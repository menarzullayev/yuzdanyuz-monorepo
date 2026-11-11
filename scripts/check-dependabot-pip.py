#!/usr/bin/env python3
"""Check that Dependabot's dependency graph sees every pip requirements file.

Dependabot's pip graph job keeps a .txt file only when its name, relative to the
`directory` in .github/dependabot.yml, looks like a manifest. It drops every other
.txt as a bystander. The regexes below are copied from dependabot-core
(python/lib/dependabot/python/dependency_grapher/requirements_layers.rb).

With `directory: /packages/backend/requirements` the files were named base.txt,
dev.txt and prod.txt. None matched, so every graph job submitted an empty
snapshot. The pip dependency graph stayed at the repo's first push, and Python
alerts stayed open after the fixes were merged. With `directory: /packages/backend`
the names are requirements/base.txt etc., which match.

Usage: check-dependabot-pip.py [--config PATH] [--root PATH]
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml

MANIFEST_TXT = [
    re.compile(r'(?:[-._]|^|/)requirements[^\s]*\.txt$', re.IGNORECASE),
    re.compile(r'(?:[-._]|^|/)require(?:[-_/][^\s.]*)?\.txt$', re.IGNORECASE),
    re.compile(r'(?:[-._]|^|/)dependenc(?:y|ies)[^\s]*\.txt$', re.IGNORECASE),
    re.compile(r'(?:[-._]|^|/)depend(?:s)?(?:[-_/][^\s.]*)?\.txt$', re.IGNORECASE),
]
# A line that pins or names a package, or includes another requirements file.
REQUIREMENT_LINE = re.compile(r'^(-r|-c|--requirement|--constraint)\s|^[A-Za-z0-9][A-Za-z0-9._\-\[\],]*\s*(==|>=|<=|~=|!=|<|>|$)')


def looks_like_manifest(name: str) -> bool:
    return any(rx.search(name) for rx in MANIFEST_TXT)


def is_requirements_file(path: Path) -> bool:
    lines = [ln.split('#', 1)[0].strip() for ln in path.read_text(encoding='utf-8').splitlines()]
    lines = [ln for ln in lines if ln]
    return bool(lines) and all(REQUIREMENT_LINE.search(ln) for ln in lines)


def fetched_txt(root: Path, directory: str) -> list[str]:
    """Tracked requirements .txt files the pip fetcher passes to the grapher: those in
    `directory` and in its immediate subdirectories, named relative to `directory`."""
    rel = directory.strip('/')
    tracked = subprocess.run(
        ['git', 'ls-files', '--', f'{rel}/*.txt' if rel else '*.txt'],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout.split()
    names = []
    for path in tracked:
        name = path[len(rel) + 1 :] if rel else path
        if name.count('/') <= 1 and is_requirements_file(root / path):
            names.append(name)
    return sorted(names)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--config', type=Path)
    args = parser.parse_args()
    config = args.config or args.root / '.github' / 'dependabot.yml'

    updates = yaml.safe_load(config.read_text(encoding='utf-8')).get('updates', [])
    pip = [u for u in updates if u.get('package-ecosystem') == 'pip']
    failed = False
    for update in pip:
        directory = update.get('directory', '/')
        names = fetched_txt(args.root, directory)
        dropped = [n for n in names if not looks_like_manifest(n)]
        if not names:
            print(f'FAIL pip {directory}: no requirements .txt files found')
            failed = True
        elif dropped:
            print(
                f'FAIL pip {directory}: the Dependabot graph job drops {", ".join(dropped)} '
                '(name does not look like a requirements manifest), so it submits no '
                'dependencies and Python alerts never close. Point `directory` at the '
                'parent of a requirements/ folder, or name the files *requirements*.txt.'
            )
            failed = True
        else:
            print(f'ok   pip {directory}: {", ".join(names)}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
