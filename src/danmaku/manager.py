"""弹幕数据管理器 — 时间戳映射、NDJSON 持久化"""
import json
import os
import time

from PySide6.QtCore import QObject, Signal
from src.logger import get as _log
from src import config

log = _log("danmaku")


class DanmakuManager(QObject):
    """管理弹幕数据：时间戳转换、持久化"""

    danmaku_added = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._count: int = 0
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

    def set_filter(self, f):
        self._filter = f

    def on_raw_message(self, raw: dict):
        ts_ms = raw.get("timestamp_ms", 0)
        offset_sec = (ts_ms / 1000.0) - self._start_time
        msg_type = raw.get("msg_type", "unknown")

        if self._count < 5:
            log.info("[MSG] type=%s offset=%.3f ts_ms=%d start_time=%.3f content=%s",
                     msg_type, offset_sec, ts_ms, self._start_time,
                     raw.get("content", "")[:30])

        if offset_sec < 0:
            log.warning("[MSG] negative offset=%.3f, skipping (ts_ms=%d start_time=%.3f)",
                        offset_sec, ts_ms, self._start_time)
            return

        if msg_type == "chat" and hasattr(self, "_filter") and self._filter.is_blocked(raw.get("content", "")):
            return

        msg = dict(raw)
        msg["offset_sec"] = offset_sec
        self._count += 1

        if self._jsonl_file:
            try:
                self._jsonl_file.write(json.dumps(msg, ensure_ascii=False) + "\n")
            except OSError as e:
                log.error("[WRITE] write failed: %s", e)

        if self._count % 50 == 0:
            log.info("[MSG] total messages: %d", self._count)

        self.danmaku_added.emit(msg)

    @property
    def start_time(self) -> float:
        return self._start_time

    @property
    def ndjson_path(self) -> str:
        return self._jsonl_path

    def periodic_flush(self):
        if self._jsonl_file:
            try:
                self._jsonl_file.flush()
            except OSError:
                pass

    def clear(self):
        if self._jsonl_file:
            try:
                self._jsonl_file.close()
            except OSError:
                pass
            self._jsonl_file = None
        self._count = 0
        self._start_time = 0.0
        self._jsonl_path = ""
        log.info("[CLEAR] danmaku manager cleared")
