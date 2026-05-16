"""Minimal logger — 5 levels: INFO (most verbose) → ERROR (always shown).

Outputs to ReLive.log (cleared each launch) and stderr.
"""

import logging
import os
import sys

if getattr(sys, "frozen", False):
    _BASE = os.path.dirname(sys.executable)
else:
    _BASE = os.path.dirname(os.path.abspath(__file__))
_LOG_FILE = os.path.join(_BASE, "ReLive.log")

_INFO     = 1
_DEBUG    = 2
_WARNING  = 3
_ERROR    = 10
_CRITICAL = 10

_NAME_MAP = {
    "INFO": _INFO, "DEBUG": _DEBUG, "WARNING": _WARNING,
    "ERROR": _ERROR, "CRITICAL": _CRITICAL,
}
_NUM_MAP = {v: k for k, v in _NAME_MAP.items()}

_LEVELS = ["INFO", "DEBUG", "WARNING", "ERROR", "CRITICAL"]


class _Logger:
    """Thin wrapper so caller writes  log.info(msg, ...)  with custom levels."""

    def __init__(self, name: str):
        self._l = logging.getLogger(name)

    def info(self, msg, *args):     self._l.log(_INFO, msg, *args)
    def debug(self, msg, *args):    self._l.log(_DEBUG, msg, *args)
    def warning(self, msg, *args):  self._l.log(_WARNING, msg, *args)
    def error(self, msg, *args):    self._l.log(_ERROR, msg, *args)
    def critical(self, msg, *args): self._l.log(_CRITICAL, msg, *args)


def _init():
    for lv, num in _NAME_MAP.items():
        logging.addLevelName(num, lv)

    root = logging.getLogger()
    root.setLevel(_INFO)

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)-6s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        fh = logging.FileHandler(_LOG_FILE, mode="w", encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)


_init()


def get(name: str) -> _Logger:
    return _Logger(name)


def set_level(level: str):
    level = level.upper()
    if level in _NAME_MAP:
        logging.getLogger().setLevel(_NAME_MAP[level])


def levels() -> list[str]:
    return _LEVELS.copy()


def current_level() -> str:
    return _NUM_MAP.get(logging.getLogger().level, "INFO")
