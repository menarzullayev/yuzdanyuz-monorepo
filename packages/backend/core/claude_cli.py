"""Headless Claude Code calls (`claude -p`) on the local Claude subscription.

The call runs with no tools, no MCP servers and no settings, CLAUDE.md or memory,
so the model can only return text (or JSON matching a schema): text inside a
student's essay or an imported document cannot make it read files or act.
The flag set is the one tg-solver runs in production (tgsolver/llm.py).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

# Dropped from the child's environment, except _ENV_KEEP. CLAUDECODE is what a parent
# Claude Code session sets; it has no underscore, so it needs its own entry.
# CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) is how a container logs in.
_ENV_PREFIXES = ('ANTHROPIC_', 'CLAUDE_')
_ENV_DROP = {'CLAUDECODE'}
_ENV_KEEP = {'CLAUDE_CONFIG_DIR', 'CLAUDE_CODE_OAUTH_TOKEN'}


class ClaudeCLIError(RuntimeError):
    pass


@dataclass
class ClaudeCLIResult:
    text: str
    structured: dict | None = None
    usage: dict = field(default_factory=dict)
    cost_usd: float = 0.0


def _child_env() -> dict:
    # An inherited ANTHROPIC_API_KEY (or a parent Claude session's CLAUDE_* vars) makes
    # the CLI bill the API instead of the subscription ("Credit balance is too low").
    return {
        k: v
        for k, v in os.environ.items()
        if k in _ENV_KEEP or not (k.startswith(_ENV_PREFIXES) or k in _ENV_DROP)
    }


def _executable() -> str:
    from django.conf import settings

    path = getattr(settings, 'CLAUDE_CLI_PATH', '') or shutil.which('claude')
    if not path:
        raise ClaudeCLIError('claude CLI topilmadi (PATH yoki CLAUDE_CLI_PATH)')
    return path


def run(
    prompt: str,
    *,
    system: str,
    model: str,
    json_schema: dict | None = None,
    timeout: int = 300,
) -> ClaudeCLIResult:
    """One `claude -p` turn. Raises ClaudeCLIError on timeout, CLI error or bad output."""
    cmd = [
        _executable(),
        '-p',
        '--model',
        model,
        '--tools',
        '',
        '--strict-mcp-config',
        '--mcp-config',
        '{"mcpServers":{}}',
        '--disable-slash-commands',
        '--no-session-persistence',
        '--setting-sources',
        '',
        '--input-format',
        'stream-json',
        '--output-format',
        'stream-json',
        '--verbose',
        # A file, not an argument: Windows caps the command line at 32767 characters.
        '--system-prompt-file',
        '',
    ]
    if json_schema is not None:
        cmd += ['--json-schema', json.dumps(json_schema)]
    message = {'type': 'user', 'message': {'role': 'user', 'content': prompt}}

    fd, prompt_file = tempfile.mkstemp(prefix='yuzdanyuz_', suffix='.md')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(system)
        cmd[cmd.index('--system-prompt-file') + 1] = prompt_file
        # Fixed flags plus the configured executable; untrusted text only goes in via stdin.
        proc = subprocess.run(  # noqa: S603
            cmd,
            input=json.dumps(message) + '\n',
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=timeout,
            env=_child_env(),
            cwd=tempfile.gettempdir(),
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except subprocess.TimeoutExpired:
        raise ClaudeCLIError(f'{model}: timeout after {timeout}s') from None
    finally:
        try:
            os.remove(prompt_file)
        except OSError:
            pass

    result = None
    for line in proc.stdout.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get('type') == 'result':
            result = obj
    if result is None:
        raise ClaudeCLIError(f'{model}: no result (rc={proc.returncode}) {proc.stderr[:300]}')
    if result.get('is_error'):
        raise ClaudeCLIError(f'{model}: {str(result.get("result"))[:300]}')

    structured = result.get('structured_output')
    if json_schema is not None and not isinstance(structured, dict):
        raise ClaudeCLIError(f'{model}: no structured output: {str(result.get("result"))[:300]}')
    return ClaudeCLIResult(
        text=result.get('result') or '',
        structured=structured if isinstance(structured, dict) else None,
        usage=result.get('usage') or {},
        cost_usd=result.get('total_cost_usd') or 0.0,
    )
