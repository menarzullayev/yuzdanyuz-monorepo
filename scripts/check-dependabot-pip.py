#!/usr/bin/env python3
"""Check that GitHub's Dependabot graph job keeps every pip requirements file.

GitHub runs the pip graph job in each directory where it finds requirements
files (here /packages/backend/requirements), whatever `directory` says in
.github/dependabot.yml. In that directory the job keeps a .txt only when

  * its name looks like a manifest (the regexes below, copied from
    dependabot-core python/lib/dependabot/python/dependency_grapher/
    requirements_layers.rb), or
  * a kept file pulls it in with `-r` / `-c`.

Every other .txt is dropped as a bystander. With only base.txt, dev.txt and
prod.txt in requirements/ (now common.txt, development.txt and production.txt),
all three were dropped: each graph job submitted
`"manifests": {}`, the pip dependency graph stayed at the repo's first push, and
Python alerts stayed open after the fixes were merged. requirements/requirements.txt
(`-r production.txt`, `-r development.txt`) is the kept entry point that pulls
in the rest.

Usage: check-dependabot-pip.py [--root PATH]
"""

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path, PurePosixPath

MANIFEST_TXT = [
    re.compile(r'(?:[-._]|^|/)requirements[^\s]*\.txt$', re.IGNORECASE),
    re.compile(r'(?:[-._]|^|/)require(?:[-_/][^\s.]*)?\.txt$', re.IGNORECASE),
    re.compile(r'(?:[-._]|^|/)dependenc(?:y|ies)[^\s]*\.txt$', re.IGNORECASE),
    re.compile(r'(?:[-._]|^|/)depend(?:s)?(?:[-_/][^\s.]*)?\.txt$', re.IGNORECASE),
]
# dependabot-core SharedFileFetcher::CHILD_REQUIREMENT_REGEX / CONSTRAINT_REGEX.
REFERENCE = re.compile(r'^-[rc]\s?(?P<path>.*\.(?:txt|in))', re.MULTILINE)
# A line that pins or names a package, or includes another requirements file.
REQUIREMENT_LINE = re.compile(
    r'^(-r|-c|--requirement|--constraint)\s'
    r'|^[A-Za-z0-9][A-Za-z0-9._\-\[\],]*\s*(==|>=|<=|~=|!=|<|>|$)'
)
SKIP_DIRS = {'node_modules', 'venv', '.venv', 'site-packages'}


def looks_like_manifest(name: str) -> bool:
    return any(rx.search(name) for rx in MANIFEST_TXT)


def is_requirements_file(text: str) -> bool:
    lines = [ln.split('#', 1)[0].strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return bool(lines) and all(REQUIREMENT_LINE.search(ln) for ln in lines)


def requirement_dirs(root: Path) -> dict[str, dict[str, str]]:
    """Tracked requirements .txt files grouped by directory: {dir: {name: text}}."""
    tracked = subprocess.run(
        ['git', 'ls-files', '--', '*.txt'], cwd=root, capture_output=True, text=True, check=True
    ).stdout.split()
    dirs: dict[str, dict[str, str]] = defaultdict(dict)
    for path in tracked:
        p = PurePosixPath(path)
        if SKIP_DIRS & set(p.parts):
            continue
        text = (root / path).read_text(encoding='utf-8', errors='replace')
        if is_requirements_file(text):
            dirs[str(p.parent)][p.name] = text
    return dirs


def kept(files: dict[str, str]) -> set[str]:
    """Names the graph job keeps: manifest-looking names plus their -r/-c closure."""
    keep = {name for name in files if looks_like_manifest(name)}
    queue = list(keep)
    while queue:
        for match in REFERENCE.finditer(files[queue.pop()]):
            ref = str(PurePosixPath(match['path'].strip()))
            if ref in files and ref not in keep:
                keep.add(ref)
                queue.append(ref)
    return keep


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()

    dirs = requirement_dirs(args.root)
    if not dirs:
        print('FAIL no tracked pip requirements files found')
        return 1
    failed = False
    for directory, files in sorted(dirs.items()):
        dropped = sorted(set(files) - kept(files))
        # Each manifest-looking file becomes its own manifest ("requirements layer"),
        # and a file that -r's another repeats its packages, so every alert is
        # reported once per such file. One entry point keeps it at one.
        entry_points = sorted(name for name in files if looks_like_manifest(name))
        if dropped:
            print(
                f'FAIL {directory}: the Dependabot graph job drops {", ".join(dropped)} '
                '(name does not look like a manifest and no kept file pulls it in with -r), '
                'so their dependencies never reach the dependency graph. Add a requirements.txt '
                'there that `-r`s them.'
            )
            failed = True
        elif len(entry_points) > 1:
            print(
                f'FAIL {directory}: {", ".join(entry_points)} all look like manifests, so the '
                f'graph job reports each shared package {len(entry_points)} times. Keep one '
                'requirements.txt entry point and give the other files plain names.'
            )
            failed = True
        else:
            print(f'ok   {directory}: {", ".join(sorted(files))}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
