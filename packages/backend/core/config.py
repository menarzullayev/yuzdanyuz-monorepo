"""ISSUE-404 — Per-tenant config with platform defaults.

Usage:
    from core.config import get_org_setting
    max_strikes = get_org_setting(org, 'anti_cheat.max_strikes')
    reward = get_org_setting(org, 'rewards.streak_milestone_coins', default=50)

Defaults live in `DEFAULTS` below. Per-org override via `Organization.settings`
(nested dict, dot notation for keys).
"""

# Platform-wide defaults. Per-org override in Organization.settings JSONField.
DEFAULTS = {
    'anti_cheat': {
        'max_strikes': 3,
        'heartbeat_timeout_seconds': 30,
    },
    'rewards': {
        'streak_milestone_coins': 50,
        'league_promote_top': 10,
        'league_demote_bottom': 10,
    },
    'disputes': {
        'quarantine_min_disputes': 5,
    },
    'exam': {
        'allow_late_join_seconds': 0,
    },
}

_SENTINEL = object()


def _walk(d: dict, dotted_key: str, default=_SENTINEL):
    parts = dotted_key.split('.')
    cur = d
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def get_org_setting(org, key: str, default=_SENTINEL):
    """Resolve a config key for an org.

    Lookup order: org.settings → DEFAULTS → explicit `default` arg → KeyError.
    """
    if org is not None and org.settings:
        value = _walk(org.settings, key, default=_SENTINEL)
        if value is not _SENTINEL:
            return value
    value = _walk(DEFAULTS, key, default=_SENTINEL)
    if value is not _SENTINEL:
        return value
    if default is not _SENTINEL:
        return default
    raise KeyError(f'Unknown config key: {key} (not in DEFAULTS, no override)')


def _collect_paths(d: dict, prefix: str = '') -> set[str]:
    """Recursively collect all dotted leaf paths from DEFAULTS."""
    paths: set[str] = set()
    for k, v in d.items():
        path = f'{prefix}.{k}' if prefix else k
        if isinstance(v, dict):
            paths |= _collect_paths(v, path)
        else:
            paths.add(path)
    return paths


_VALID_PATHS = _collect_paths(DEFAULTS)


def _flatten(d: dict, prefix: str = '') -> list[str]:
    """Flatten a nested settings dict into dotted leaf paths."""
    out: list[str] = []
    if not isinstance(d, dict):
        return out
    for k, v in d.items():
        path = f'{prefix}.{k}' if prefix else k
        if isinstance(v, dict):
            out.extend(_flatten(v, path))
        else:
            out.append(path)
    return out


# Legacy top-level keys from apps.organizations.models.DEFAULT_ORG_SETTINGS that
# predate ISSUE-404 and are validated by other code paths. Don't flag them here
# to keep backward compatibility.
_LEGACY_TOPLEVEL_KEYS = frozenset(
    {
        'approval_required',
        'ai_enabled',
        'max_students',
        'proctoring',
        'allow_public_exams',
        'trial_days',
    }
)


def validate_org_settings(settings_dict) -> list[str]:
    """Validate a settings override dict against DEFAULTS schema.

    Returns a list of human-readable error messages. Empty list = OK.

    Rules:
      - Top-level keys must be a DEFAULTS group OR a known legacy key.
      - Under DEFAULTS groups, every leaf must be a known dotted path.
    """
    errors: list[str] = []
    if not settings_dict:
        return errors
    if not isinstance(settings_dict, dict):
        return [f'settings must be a dict, got {type(settings_dict).__name__}']
    managed_groups = set(DEFAULTS.keys())
    for top_key, value in settings_dict.items():
        if top_key in _LEGACY_TOPLEVEL_KEYS:
            continue
        if top_key not in managed_groups:
            errors.append(f"Unknown top-level setting key '{top_key}' (not in DEFAULTS)")
            continue
        if not isinstance(value, dict):
            errors.append(f"Setting group '{top_key}' must be a dict, got {type(value).__name__}")
            continue
        for path in _flatten({top_key: value}):
            if path not in _VALID_PATHS:
                errors.append(f"Unknown setting key '{path}' (not in DEFAULTS)")
    return errors
