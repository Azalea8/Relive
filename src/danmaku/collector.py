"""弹幕采集器 — 管理 Node.js 子进程，读取 stdout 解析 JSON"""
import json
import os
import subprocess
import sys
import threading

from PyQt6.QtCore import QObject, pyqtSignal
from src.logger import get as _log
from pathlib import Path

log = _log("danmaku")

if getattr(sys, 'frozen', False):
    _EXE_DIR = os.path.dirname(sys.executable)
    _NODE_CMD = os.path.join(_EXE_DIR, 'bin', 'node.exe')
    _WORKER_JS = os.path.join(_EXE_DIR, 'danmaku', 'douyu_worker.js')
else:
    _NODE_CMD = 'node'
    _WORKER_JS = str(Path(__file__).parent / 'douyu_worker.js')


class DanmakuCollector(QObject):
    """管理 douyu_worker.js 子进程，解析弹幕 JSON 并发射信号"""

    message_received = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process: subprocess.Popen | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._running = False

    def start(self, room_id: str):
        if self._running:
            self.stop()

        log.info("[START] starting danmaku worker for room=%s", room_id)

        cmd = [_NODE_CMD, _WORKER_JS, room_id]
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creationflags,
            )
        except FileNotFoundError:
            log.error("[START] node executable not found")
            self.error_occurred.emit("Node.js 未安装，弹幕功能不可用")
            return
        except Exception as e:
            log.error("[START] Popen failed: %s", e)
            self.error_occurred.emit(str(e))
            return

        self._running = True

        self._stdout_thread = threading.Thread(
            target=self._read_stdout, daemon=True,
        )
        self._stdout_thread.start()

        self._stderr_thread = threading.Thread(
            target=self._read_stderr, daemon=True,
        )
        self._stderr_thread.start()

        log.info("[START] danmaku worker started, PID=%d", self._process.pid)

    def stop(self):
        self._running = False
        if self._process and self._process.poll() is None:
            log.info("[STOP] terminating danmaku worker PID=%d", self._process.pid)
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()

        if self._stdout_thread and self._stdout_thread.is_alive():
            self._stdout_thread.join(timeout=2)
        if self._stderr_thread and self._stderr_thread.is_alive():
            self._stderr_thread.join(timeout=2)

        self._process = None
        log.info("[STOP] danmaku worker stopped")

    def _read_stdout(self):
        proc = self._process
        if not proc:
            log.warning("[STDOUT] no process, abort")
            return
        log.info("[STDOUT] reader thread started")
        count = 0
        try:
            for raw_line in proc.stdout:
                if not self._running:
                    log.info("[STDOUT] not running, stop reading")
                    break
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                count += 1
                if count <= 3:
                    log.info("[STDOUT] raw line #%d: %s", count, line[:200])
                try:
                    data = json.loads(line)
                    if count <= 3:
                        log.info("[STDOUT] parsed msg_type=%s content=%s",
                                 data.get("msg_type"), data.get("content", "")[:50])
                    self.message_received.emit(data)
                except json.JSONDecodeError as e:
                    log.warning("[STDOUT] JSON parse error: %s | line: %s", e, line[:100])
            log.info("[STDOUT] reader thread ended, total lines=%d", count)
        except (ValueError, OSError) as e:
            log.info("[STDOUT] drain ended: %s (total lines=%d)", e, count)

    def _read_stderr(self):
        proc = self._process
        if not proc:
            return
        try:
            for raw_line in proc.stderr:
                if not self._running:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if line:
                    log.debug("[node] %s", line)
        except (ValueError, OSError):
            pass
