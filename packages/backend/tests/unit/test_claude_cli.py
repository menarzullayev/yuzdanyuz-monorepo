"""core.claude_cli: the `claude -p` subscription backend, with subprocess.run faked.

The safety properties are the point: no tools, no MCP servers, no settings or
CLAUDE.md, the system prompt in a file, and no ANTHROPIC_*/CLAUDE_* variables
leaking into the child (they make the CLI bill an API key instead of the
subscription).
"""

import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime

import pytest
from prometheus_client import REGISTRY

from core import claude_cli

SCHEMA = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}}


def _result_line(**overrides) -> str:
    result = {
        'type': 'result',
        'subtype': 'success',
        'is_error': False,
        'result': 'Javob matni',
        'usage': {'input_tokens': 12, 'output_tokens': 7},
        'total_cost_usd': 0.0012,
    }
    result.update(overrides)
    return json.dumps(result)


@pytest.fixture
def fake_cli(monkeypatch, settings):
    settings.CLAUDE_CLI_PATH = '/opt/claude/bin/claude'
    seen = {'stdout': '{"type":"system"}\n' + _result_line() + '\n', 'stderr': '', 'raise': None}

    def fake_run(cmd, **kwargs):
        if seen['raise']:
            raise seen['raise']
        seen['cmd'] = cmd
        seen['kwargs'] = kwargs
        path = cmd[cmd.index('--system-prompt-file') + 1]
        seen['prompt_path'] = path
        with open(path, encoding='utf-8') as f:
            seen['system'] = f.read()
        return subprocess.CompletedProcess(cmd, 0, stdout=seen['stdout'], stderr=seen['stderr'])

    monkeypatch.setattr(claude_cli.subprocess, 'run', fake_run)
    return seen


def _flag(cmd: list, name: str) -> str:
    return cmd[cmd.index(name) + 1]


@pytest.mark.unit
class TestClaudeCLIRun:
    def test_runs_with_no_tools_mcp_or_settings(self, fake_cli):
        claude_cli.run('salom', system='SYS', model='claude-sonnet-5-5')

        cmd = fake_cli['cmd']
        assert cmd[0] == '/opt/claude/bin/claude'
        assert cmd[1] == '-p'
        assert _flag(cmd, '--model') == 'claude-sonnet-5-5'
        assert _flag(cmd, '--tools') == ''
        assert '--strict-mcp-config' in cmd
        assert json.loads(_flag(cmd, '--mcp-config')) == {'mcpServers': {}}
        assert _flag(cmd, '--setting-sources') == ''
        assert '--disable-slash-commands' in cmd
        assert '--no-session-persistence' in cmd
        assert '--json-schema' not in cmd
        assert fake_cli['kwargs']['cwd'] == tempfile.gettempdir()

    def test_prompt_goes_on_stdin_and_system_prompt_in_a_removed_file(self, fake_cli):
        system = 'x' * 40000  # longer than the Windows command line allows
        claude_cli.run('2 + 2 = ?', system=system, model='m')

        assert fake_cli['system'] == system
        assert not os.path.exists(fake_cli['prompt_path'])
        assert '--system-prompt' not in fake_cli['cmd']
        assert sum(len(a) for a in fake_cli['cmd']) < 32767 - 5000
        message = json.loads(fake_cli['kwargs']['input'])
        assert message == {
            'type': 'user',
            'message': {'role': 'user', 'content': '2 + 2 = ?'},
        }

    def test_child_env_drops_api_keys_and_parent_claude_vars(self, fake_cli, monkeypatch):
        monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-must-not-leak')
        monkeypatch.setenv('ANTHROPIC_MODEL', 'claude-other')
        monkeypatch.setenv('CLAUDE_CODE_ENTRYPOINT', 'cli')
        monkeypatch.setenv('CLAUDECODE', '1')
        monkeypatch.setenv('ANTHROPIC_BASE_URL', 'http://127.0.0.1:9/desktop-gateway')
        monkeypatch.setenv('CLAUDE_CONFIG_DIR', '/home/app/.claude-yuzdanyuz')
        # How a container logs in to the subscription (`claude setup-token`).
        monkeypatch.setenv('CLAUDE_CODE_OAUTH_TOKEN', 'oauth-token-for-the-container')
        monkeypatch.setenv('PATH_MARKER', 'kept')

        claude_cli.run('salom', system='', model='m')

        env = fake_cli['kwargs']['env']
        for name in (
            'ANTHROPIC_API_KEY',
            'ANTHROPIC_MODEL',
            'ANTHROPIC_BASE_URL',
            'CLAUDE_CODE_ENTRYPOINT',
            'CLAUDECODE',
        ):
            assert name not in env
        assert env['CLAUDE_CONFIG_DIR'] == '/home/app/.claude-yuzdanyuz'
        assert env['CLAUDE_CODE_OAUTH_TOKEN'] == 'oauth-token-for-the-container'
        assert env['PATH_MARKER'] == 'kept'

    def test_returns_text_usage_and_cost(self, fake_cli):
        result = claude_cli.run('salom', system='', model='m')

        assert result.text == 'Javob matni'
        assert result.structured is None
        assert result.usage == {'input_tokens': 12, 'output_tokens': 7}
        assert result.cost_usd == 0.0012

    def test_json_schema_returns_structured_output(self, fake_cli):
        fake_cli['stdout'] = _result_line(structured_output={'ok': True}) + '\n'

        result = claude_cli.run('salom', system='', model='m', json_schema=SCHEMA)

        assert json.loads(_flag(fake_cli['cmd'], '--json-schema')) == SCHEMA
        assert result.structured == {'ok': True}

    def test_schema_without_structured_output_is_an_error(self, fake_cli):
        with pytest.raises(claude_cli.ClaudeCLIError, match='no structured output'):
            claude_cli.run('salom', system='', model='m', json_schema=SCHEMA)

    def test_cli_error_result_raises(self, fake_cli):
        fake_cli['stdout'] = _result_line(is_error=True, result='Usage limit reached') + '\n'

        with pytest.raises(claude_cli.ClaudeCLIError, match='Usage limit reached'):
            claude_cli.run('salom', system='', model='m')

    def test_missing_result_line_raises_with_stderr(self, fake_cli):
        fake_cli['stdout'] = 'not json\n'
        fake_cli['stderr'] = 'login required'

        with pytest.raises(claude_cli.ClaudeCLIError, match='login required'):
            claude_cli.run('salom', system='', model='m')

    def test_timeout_raises_and_still_removes_the_prompt_file(self, fake_cli, monkeypatch):
        created = []
        real_mkstemp = tempfile.mkstemp

        def tracking_mkstemp(**kw):
            fd, path = real_mkstemp(**kw)
            created.append(path)
            return fd, path

        monkeypatch.setattr(claude_cli.tempfile, 'mkstemp', tracking_mkstemp)
        fake_cli['raise'] = subprocess.TimeoutExpired('claude', 5)

        with pytest.raises(claude_cli.ClaudeCLIError, match='timeout'):
            claude_cli.run('salom', system='SYS', model='m', timeout=5)
        assert created and not os.path.exists(created[0])

    @pytest.mark.parametrize(
        ('reply', 'error'),
        [
            ('Claude AI usage limit reached|1794500000', claude_cli.ClaudeRateLimitError),
            ("You've hit your limit · resets 5pm", claude_cli.ClaudeRateLimitError),
            ('API Error: 529 Overloaded', claude_cli.ClaudeRateLimitError),
            ('Not logged in · Please run /login', claude_cli.ClaudeAuthError),
            ('OAuth token has expired', claude_cli.ClaudeAuthError),
            ('Prompt is too long', claude_cli.ClaudeCLIError),
        ],
    )
    def test_error_results_are_classified(self, fake_cli, reply, error):
        fake_cli['stdout'] = _result_line(is_error=True, result=reply) + '\n'

        with pytest.raises(claude_cli.ClaudeCLIError) as caught:
            claude_cli.run('salom', system='', model='m')

        assert type(caught.value) is error
        assert reply[:20] in str(caught.value)

    def test_usage_limit_reset_time_is_parsed(self, fake_cli):
        fake_cli['stdout'] = (
            _result_line(is_error=True, result='Claude AI usage limit reached|1794500000') + '\n'
        )

        with pytest.raises(claude_cli.ClaudeRateLimitError) as caught:
            claude_cli.run('salom', system='', model='m')

        assert caught.value.resets_at == datetime.fromtimestamp(1794500000, tz=UTC)

    def test_stderr_without_result_is_classified_too(self, fake_cli):
        fake_cli['stdout'] = 'not json\n'
        fake_cli['stderr'] = '429 Too Many Requests'

        with pytest.raises(claude_cli.ClaudeRateLimitError):
            claude_cli.run('salom', system='', model='m')

    def test_timeout_is_its_own_kind(self, fake_cli):
        fake_cli['raise'] = subprocess.TimeoutExpired('claude', 5)

        with pytest.raises(claude_cli.ClaudeTimeoutError):
            claude_cli.run('salom', system='', model='m', timeout=5)

    def test_every_call_is_counted_by_outcome(self, fake_cli):
        def count(outcome):
            return REGISTRY.get_sample_value('yz_ai_cli_calls_total', {'outcome': outcome}) or 0.0

        ok_before, auth_before = count('ok'), count('auth')
        duration_before = REGISTRY.get_sample_value('yz_ai_cli_seconds_count') or 0.0

        claude_cli.run('salom', system='', model='m')
        fake_cli['stdout'] = _result_line(is_error=True, result='Not logged in') + '\n'
        with pytest.raises(claude_cli.ClaudeAuthError):
            claude_cli.run('salom', system='', model='m')

        assert count('ok') == ok_before + 1
        assert count('auth') == auth_before + 1
        assert REGISTRY.get_sample_value('yz_ai_cli_seconds_count') == duration_before + 2

    def test_missing_executable_raises(self, settings, monkeypatch):
        settings.CLAUDE_CLI_PATH = ''
        monkeypatch.setattr(claude_cli.shutil, 'which', lambda name: None)

        with pytest.raises(claude_cli.ClaudeCLIError, match='topilmadi'):
            claude_cli.run('salom', system='', model='m')


@pytest.mark.unit
class TestGenerateTextOnClaudeCLI:
    @pytest.fixture(autouse=True)
    def _settings(self, settings):
        settings.LLM_BACKEND = 'claude_cli'
        settings.ANTHROPIC_API_KEY = ''  # the subscription needs no key
        settings.ANTHROPIC_MODEL = 'claude-sonnet-5-5'
        settings.CLAUDE_CLI_TIMEOUT = 77

    def test_text_reply(self, monkeypatch):
        from apps.intelligence import tasks

        calls = []

        def fake_run(prompt, **kw):
            calls.append((prompt, kw))
            return claude_cli.ClaudeCLIResult(text='Davom et!')

        monkeypatch.setattr(claude_cli, 'run', fake_run)

        assert tasks._generate_text('prompt', system='SYS') == 'Davom et!'
        assert calls == [
            (
                'prompt',
                {'system': 'SYS', 'model': 'claude-sonnet-5-5', 'json_schema': None, 'timeout': 77},
            )
        ]

    def test_schema_reply_is_returned_as_json_text(self, monkeypatch):
        from apps.intelligence import tasks

        scores = {
            'grammar': 80,
            'content': 70,
            'structure': 75,
            'relevance': 90,
            'rationale': 'Yaxshi',
        }
        monkeypatch.setattr(
            claude_cli,
            'run',
            lambda prompt, **kw: claude_cli.ClaudeCLIResult(text='', structured=scores),
        )

        text = tasks._generate_text('p', json_schema=tasks.ESSAY_RUBRIC_SCHEMA)

        assert json.loads(text) == scores


@pytest.mark.unit
class TestClaudeCLIParserProvider:
    def test_provider_returns_structured_question(self, monkeypatch, settings):
        from apps.catalog.services.ai_providers.claude_cli import PARSER_SCHEMA, ClaudeCLIProvider

        seen = {}
        question = {'text': 'Q', 'options': [{'label': 'A', 'text': 'x', 'is_correct': True}]}

        def fake_run(prompt, **kw):
            seen.update(kw, prompt=prompt)
            return claude_cli.ClaudeCLIResult(text='', structured=question)

        monkeypatch.setattr(claude_cli, 'run', fake_run)

        assert ClaudeCLIProvider(model='claude-sonnet-5-5').parse_text_block('blok') == question
        assert seen['json_schema'] == PARSER_SCHEMA
        assert seen['model'] == 'claude-sonnet-5-5'
        assert 'blok' in seen['prompt']

    def test_provider_turns_cli_errors_into_error_dict(self, monkeypatch):
        from apps.catalog.services.ai_providers.claude_cli import ClaudeCLIProvider

        def boom(prompt, **kw):
            raise claude_cli.ClaudeCLIError('m: timeout after 300s')

        monkeypatch.setattr(claude_cli, 'run', boom)

        assert ClaudeCLIProvider(model='m').parse_text_block('blok') == {
            'error': 'm: timeout after 300s',
            'raw_text': 'blok',
        }

    def test_factory_builds_the_cli_provider(self):
        from apps.catalog.models import AIProviderConfig
        from apps.catalog.services.ai_providers import ProviderFactory
        from apps.catalog.services.ai_providers.claude_cli import ClaudeCLIProvider

        config = AIProviderConfig(
            provider=AIProviderConfig.Provider.CLAUDE_CLI, model='claude-sonnet-5-5'
        )

        assert isinstance(ProviderFactory.create(config), ClaudeCLIProvider)


@pytest.mark.integration
class TestAIParserDefaultsToClaudeCLI:
    def test_no_configured_provider_falls_back_to_the_subscription(self, db, monkeypatch, settings):
        from apps.catalog.services.ai_parser import AIParserService

        settings.LLM_BACKEND = 'claude_cli'
        settings.ANTHROPIC_MODEL = 'claude-sonnet-5-5'
        question = {'text': 'Q', 'options': []}
        monkeypatch.setattr(
            claude_cli,
            'run',
            lambda prompt, **kw: claude_cli.ClaudeCLIResult(text='', structured=question),
        )

        assert AIParserService().parse_text_block('blok') == question

    def test_api_backend_without_provider_still_reports_missing_config(
        self, db, monkeypatch, settings
    ):
        from apps.catalog.services.ai_parser import AIParserService

        settings.LLM_BACKEND = 'anthropic_api'

        def must_not_run(prompt, **kw):
            raise AssertionError('claude -p must not run on the API backend')

        monkeypatch.setattr(claude_cli, 'run', must_not_run)

        assert 'sozlanmagan' in AIParserService().parse_text_block('blok')['error']
