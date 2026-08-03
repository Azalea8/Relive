"""Danmaku content filter — keyword and regex matching."""
import os
import re

from src.logger import get as _log

log = _log("filter")


class DanmakuFilter:
    def __init__(self, rules_path: str):
        self._path = rules_path
        self._patterns: list[re.Pattern] = []
        self._raw_rules: list[str] = []
        self.load()

    def load(self):
        self._patterns.clear()
        self._raw_rules.clear()
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f if l.strip()]
        except OSError:
            return
        for line in lines:
            self._raw_rules.append(line)
            try:
                self._patterns.append(re.compile(line))
            except re.error:
                self._patterns.append(re.compile(re.escape(line)))
        log.info("loaded %d rules from %s", len(self._patterns), self._path)

    def save(self, rules: list[str]):
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            f.write("\n".join(rules) + "\n")
        self.load()
        log.info("saved %d rules to %s", len(rules), self._path)

    def is_blocked(self, text: str) -> bool:
        for pattern in self._patterns:
            m = pattern.search(text)
            if m:
                return True
        return False

    def matching_rule(self, text: str) -> str:
        for pattern in self._patterns:
            m = pattern.search(text)
            if m:
                return pattern.pattern
        return ""

    @property
    def rule_count(self) -> int:
        return len(self._patterns)

    @property
    def rules(self) -> list[str]:
        return list(self._raw_rules)
