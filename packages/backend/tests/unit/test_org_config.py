"""ISSUE-404 — tests for core.config (per-tenant config helper)."""

import pytest

from core.config import DEFAULTS, get_org_setting, validate_org_settings


@pytest.mark.unit
class TestDefaults:
    """DEFAULTS schema sanity checks."""

    def test_defaults_has_required_top_level_groups(self):
        """DEFAULTS dict has all required keys for ISSUE-404."""
        assert 'anti_cheat' in DEFAULTS
        assert 'rewards' in DEFAULTS
        assert 'disputes' in DEFAULTS
        assert 'exam' in DEFAULTS

    def test_defaults_has_required_leaf_keys(self):
        """Each group has the expected leaf keys with sane values."""
        assert DEFAULTS['anti_cheat']['max_strikes'] == 3
        assert DEFAULTS['anti_cheat']['heartbeat_timeout_seconds'] == 30
        assert DEFAULTS['rewards']['streak_milestone_coins'] == 50
        assert DEFAULTS['rewards']['league_promote_top'] == 10
        assert DEFAULTS['rewards']['league_demote_bottom'] == 10
        assert DEFAULTS['disputes']['quarantine_min_disputes'] == 5
        assert DEFAULTS['exam']['allow_late_join_seconds'] == 0


@pytest.mark.unit
class TestGetOrgSetting:
    """get_org_setting() lookup behaviour."""

    def test_none_org_returns_default(self):
        """get_org_setting(None, ...) falls through to DEFAULTS."""
        assert get_org_setting(None, 'anti_cheat.max_strikes') == 3

    def test_empty_settings_returns_default(self, org):
        """Org with empty `settings` dict returns DEFAULTS value."""
        org.settings = {}
        org.save()
        assert get_org_setting(org, 'anti_cheat.max_strikes') == 3

    def test_org_override_wins(self, org):
        """Per-org override beats DEFAULTS."""
        org.settings = {'anti_cheat': {'max_strikes': 1}}
        org.save()
        assert get_org_setting(org, 'anti_cheat.max_strikes') == 1

    def test_partial_override_falls_back_to_default(self, org):
        """Override on one key in a group leaves siblings on DEFAULTS."""
        org.settings = {'anti_cheat': {'max_strikes': 7}}
        org.save()
        assert get_org_setting(org, 'anti_cheat.max_strikes') == 7
        assert get_org_setting(org, 'anti_cheat.heartbeat_timeout_seconds') == 30

    def test_unknown_key_no_default_raises(self):
        """Unknown key with no explicit default raises KeyError."""
        with pytest.raises(KeyError):
            get_org_setting(None, 'nonexistent.key')

    def test_unknown_key_with_default_returns_default(self):
        """Unknown key with explicit default returns it."""
        assert get_org_setting(None, 'nonexistent.key', default=42) == 42

    def test_nested_dotted_path_three_levels(self, org):
        """Resolution handles 3+ level nesting via dot notation."""
        org.settings = {'a': {'b': {'c': 'deep-value'}}}
        org.save()
        assert get_org_setting(org, 'a.b.c') == 'deep-value'


@pytest.mark.unit
class TestValidateOrgSettings:
    """validate_org_settings() schema check."""

    def test_empty_settings_valid(self):
        """Empty dict produces no errors."""
        assert validate_org_settings({}) == []

    def test_valid_override_passes(self):
        """A correct override returns no errors."""
        errors = validate_org_settings({'anti_cheat': {'max_strikes': 1}})
        assert errors == []

    def test_unknown_top_level_key_flagged(self):
        """Unknown top-level key (not legacy, not DEFAULTS) is flagged."""
        errors = validate_org_settings({'totally_unknown_group': {'k': 1}})
        assert len(errors) == 1
        assert 'totally_unknown_group' in errors[0]

    def test_unknown_leaf_in_known_group_flagged(self):
        """Unknown leaf inside a known DEFAULTS group is flagged."""
        errors = validate_org_settings({'anti_cheat': {'made_up_key': 9}})
        assert len(errors) == 1
        assert 'anti_cheat.made_up_key' in errors[0]

    def test_legacy_keys_allowed(self):
        """Legacy DEFAULT_ORG_SETTINGS keys are not flagged (backwards compat)."""
        legacy = {'max_students': 100, 'ai_enabled': False, 'proctoring': 'strict'}
        assert validate_org_settings(legacy) == []
