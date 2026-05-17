"""弹幕数据管理器 — 时间戳映射、NDJSON 持久化、时间范围查询"""
import bisect
import json
import os
import time

from PyQt6.QtCore import QObject, pyqtSignal
from logger import get as _log
import config

log = _log("danmaku")


class DanmakuManager(QObject):
    """管理弹幕数据：时间戳转换、持久化、查询"""

    danmaku_added = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._messages: list[dict] = []
        self._offsets: list[float] = []  # parallel bisect key list
        self._start_time: float = 0.0
        self._jsonl_path: str = ""
        self._jsonl_file = None

    def set_recording_start(self, start_time: float):
        self._start_time = start_time
        os.makedirs(config.DANMAKU_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        self._jsonl_path = os.path.join(config.DANMAKU_DIR, f"session_{ts}.ndjson")
        self._jsonl_file = open(self._jsonl_path, "a", encoding="utf-8")
        log.info("[SET_START] start_time=%.3f file=%s", start_time, self._jsonl_path)

    def on_raw_message(self, raw: dict):
        ts_ms = raw.get("timestamp_ms", 0)
        offset_sec = (ts_ms / 1000.0) - self._start_time
        msg_type = raw.get("msg_type", "unknown")

        if len(self._messages) < 5:
            log.info("[MSG] type=%s offset=%.3f ts_ms=%d start_time=%.3f content=%s",
                     msg_type, offset_sec, ts_ms, self._start_time,
                     raw.get("content", "")[:30])

        if offset_sec < 0:
            log.warning("[MSG] negative offset=%.3f, skipping (ts_ms=%d start_time=%.3f)",
                        offset_sec, ts_ms, self._start_time)
            return

        msg = dict(raw)
        msg["offset_sec"] = offset_sec

        self._messages.append(msg)
        self._offsets.append(offset_sec)

        if self._jsonl_file:
            try:
                self._jsonl_file.write(json.dumps(msg, ensure_ascii=False) + "\n")
                self._jsonl_file.flush()
            except OSError as e:
                log.error("[FLUSH] write failed: %s", e)

        if len(self._messages) % 50 == 0:
            log.info("[MSG] total messages: %d", len(self._messages))

        self.danmaku_added.emit(msg)

    def query_range(self, start_sec: float, end_sec: float) -> list[dict]:
        if not self._offsets:
            return []
        left = bisect.bisect_left(self._offsets, start_sec)
        right = bisect.bisect_right(self._offsets, end_sec)
        return self._messages[left:right]

    def get_all(self) -> list[dict]:
        return list(self._messages)

    @property
    def start_time(self) -> float:
        return self._start_time

    @property
    def ndjson_path(self) -> str:
        return self._jsonl_path

    def clear(self):
        if self._jsonl_file:
            try:
                self._jsonl_file.close()
            except OSError:
                pass
            self._jsonl_file = None
        self._messages.clear()
        self._offsets.clear()
        self._start_time = 0.0
        self._jsonl_path = ""
        log.info("[CLEAR] danmaku manager cleared")
