"""Apply a LOGGING dict inside a test and put every logger back afterwards.

dictConfig() changes handlers, levels, propagate and `disabled` on loggers it does
not even name; re-applying settings.LOGGING is not enough (the test settings use
disable_existing_loggers=True, which would silence loggers other tests capture).
"""

from __future__ import annotations

import contextlib
import io
import logging
import logging.config
from collections.abc import Iterator
from typing import Any


def _state(logger: logging.Logger) -> tuple:
    return (
        list(logger.handlers),
        logger.level,
        logger.propagate,
        logger.disabled,
        list(logger.filters),
    )


@contextlib.contextmanager
def applied_logging(config: dict[str, Any]) -> Iterator[None]:
    manager = logging.root.manager
    saved = {
        name: _state(lg)
        for name, lg in list(manager.loggerDict.items())
        if isinstance(lg, logging.Logger)
    }
    saved_root = _state(logging.root)
    try:
        logging.config.dictConfig(config)
        yield
    finally:
        for name, lg in list(manager.loggerDict.items()):
            if not isinstance(lg, logging.Logger):
                continue
            if name in saved:
                lg.handlers, lg.level, lg.propagate, lg.disabled, lg.filters = (
                    saved[name][0],
                    saved[name][1],
                    saved[name][2],
                    saved[name][3],
                    saved[name][4],
                )
            else:
                lg.handlers, lg.level, lg.propagate, lg.disabled = [], logging.NOTSET, True, False
        (logging.root.handlers, logging.root.level, _, _, logging.root.filters) = saved_root


@contextlib.contextmanager
def prod_json_logging(level: str = 'INFO') -> Iterator[io.StringIO]:
    """The real production LOGGING dict, with its console handler writing to a buffer."""
    from core import observability

    buf = io.StringIO()
    config = observability.json_logging_dict(level=level)
    config['handlers']['console']['stream'] = buf
    with applied_logging(config):
        yield buf
