"""Tiny User-Agent parser: enough to label a device in the sessions list.

Not a general UA parser on purpose (no new dependency); unknown agents fall
back to a generic label rather than failing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Order matters: the first match wins (Edge/Opera/etc. also contain "Chrome").
_BROWSERS = (
    ('Telegram', re.compile(r'Telegram', re.I)),
    ('Edge', re.compile(r'Edg(e|A|iOS)?/', re.I)),
    ('Opera', re.compile(r'OPR/|Opera', re.I)),
    ('Yandex Browser', re.compile(r'YaBrowser/', re.I)),
    ('Samsung Internet', re.compile(r'SamsungBrowser/', re.I)),
    ('Firefox', re.compile(r'Firefox/|FxiOS/', re.I)),
    ('Chrome', re.compile(r'Chrome/|CriOS/', re.I)),
    ('Safari', re.compile(r'Safari/', re.I)),
)

_OSES = (
    ('Android', re.compile(r'Android', re.I)),
    ('iOS', re.compile(r'iPhone|iPad|iPod', re.I)),
    ('Windows', re.compile(r'Windows', re.I)),
    ('macOS', re.compile(r'Macintosh|Mac OS X', re.I)),
    ('Linux', re.compile(r'Linux|X11', re.I)),
)


@dataclass(frozen=True)
class ParsedUA:
    browser: str
    os: str
    device_type: str  # 'mobile' | 'desktop' | 'unknown'

    @property
    def label(self) -> str:
        if self.browser == 'Unknown' and self.os == 'Unknown':
            return 'Unknown device'
        if self.os == 'Unknown':
            return self.browser
        if self.browser == 'Unknown':
            return self.os
        return f'{self.browser} on {self.os}'


def parse_user_agent(ua: str | None) -> ParsedUA:
    ua = (ua or '')[:300]
    browser = next((name for name, rx in _BROWSERS if rx.search(ua)), 'Unknown')
    os_name = next((name for name, rx in _OSES if rx.search(ua)), 'Unknown')
    if os_name in ('Android', 'iOS') or re.search(r'Mobile', ua):
        device_type = 'mobile'
    elif os_name != 'Unknown':
        device_type = 'desktop'
    else:
        device_type = 'unknown'
    return ParsedUA(browser=browser, os=os_name, device_type=device_type)
