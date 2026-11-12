"""Username rules: 3-32 chars of [a-z0-9_.], case-insensitive unique, reserved words blocked."""

import re
import secrets
import unicodedata

from apps.accounts.models import CustomUser

USERNAME_RE = re.compile(r'^[a-z0-9_.]{3,32}$')

RESERVED_USERNAMES = frozenset(
    {
        'admin', 'administrator', 'support', 'root', 'api', 'staff', 'mashqgoh', 'help',
        'moderator', 'mod', 'system', 'official', 'yuzdanyuz', 'milliy', 'sertifikat',
        'info', 'contact', 'billing', 'security', 'telegram', 'bot', 'null', 'undefined',
        'none', 'me', 'www', 'mail', 'noreply', 'team', 'owner', 'superuser', 'settings',
        'login', 'logout', 'register', 'signup', 'signin', 'auth', 'static', 'media', 'test',
    }
)  # fmt: skip

_CYR = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo', 'ж': 'j',
    'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
    'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'x', 'ц': 's',
    'ч': 'ch', 'ш': 'sh', 'щ': 'sh', 'ъ': '', 'ы': 'i', 'ь': '', 'э': 'e', 'ю': 'yu',
    'я': 'ya', 'ў': 'o', 'қ': 'q', 'ғ': 'g', 'ҳ': 'h',
}  # fmt: skip


def normalize_username(raw: str) -> str:
    return (raw or '').strip().lower()


def is_reserved(name: str) -> bool:
    n = normalize_username(name)
    return n in RESERVED_USERNAMES or re.sub(r'[._]', '', n) in RESERVED_USERNAMES


def username_format_error(name: str) -> tuple[str, str] | None:
    """``(code, message)`` when ``name`` breaks the format/reserved rules, else None."""
    if not USERNAME_RE.match(name or ''):
        return (
            'username_invalid',
            'Username must be 3-32 characters: lowercase letters, digits, "_" and ".".',
        )
    if is_reserved(name):
        return ('username_reserved', 'This username is reserved.')
    return None


def is_taken(name: str, exclude_pk=None) -> bool:
    qs = CustomUser.objects.filter(username__iexact=normalize_username(name))
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return qs.exists()


def _slug(text: str) -> str:
    text = ''.join(_CYR.get(ch, ch) for ch in (text or '').lower())
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9_.]+', '', text.replace(' ', '.'))


def _candidates(base: str):
    yield base
    for _ in range(40):
        suffix = str(secrets.randbelow(900) + 100)
        yield f'{base[: 32 - len(suffix)]}{suffix}'
    while True:
        yield f'user{secrets.randbelow(10**8):08d}'


def available(name: str) -> bool:
    return username_format_error(name) is None and not is_taken(name)


def suggest_usernames(base: str, count: int = 3) -> list[str]:
    """Up to ``count`` free, valid usernames derived from ``base``."""
    base = _slug(base).strip('._')[:28] or 'user'
    if len(base) < 3:
        base = (base + 'user')[:6]
    out: list[str] = []
    for cand in _candidates(base):
        if cand not in out and cand != base and available(cand):
            out.append(cand)
        if len(out) == count:
            break
    return out


def generate_username(first_name: str = '', last_name: str = '', fallback: str = 'user') -> str:
    """Free username for Telegram/Google signups, from the person's name."""
    base = _slug(f'{first_name} {last_name}'.strip()).strip('._')[:28]
    base = re.sub(r'\.{2,}', '.', base)
    if len(base) < 3:
        base = _slug(fallback).strip('._')[:28] or 'user'
        if len(base) < 3:
            base = 'user'
    for cand in _candidates(base):
        if available(cand):
            return cand
    raise RuntimeError('unreachable')  # pragma: no cover
