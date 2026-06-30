"""弹幕采集器 — 管理 Go 子进程，读取 stdout 解析 JSON"""
import json
import os
import subprocess
import sys
import threading

from PySide6.QtCore import QObject, Signal
from src.logger import get as _log
from pathlib import Path

log = _log("danmaku")

if getattr(sys, 'frozen', False):
    _EXE_DIR = os.path.dirname(sys.executable)
    _DANMAKU_GO_EXE = os.path.join(_EXE_DIR, 'bin', 'danmaku_worker.exe')
else:
    _DANMAKU_GO_DIR = str(Path(__file__).parent / 'danmaku_go')


class DanmakuCollector(QObject):
    """管理 Go 子进程，解析弹幕 JSON 并发射信号"""

    message_received = Signal(dict)
    error_occurred = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process: subprocess.Popen | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._running = False

    def start(self, room_id: str, platform: str = "douyu"):
        if self._running:
            self.stop()

        cmd, cwd = self._go_cmd(platform, room_id)

        if not cmd:
            return

        log.info("[START] starting danmaku worker for room=%s platform=%s", room_id, platform)

        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                creationflags=creationflags,
            )
        except FileNotFoundError as e:
            log.error("[START] executable not found: %s", e)
            self.error_occurred.emit(f"弹幕worker未找到: {e}")
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

    def _go_cmd(self, platform: str, room_id: str) -> tuple[list[str], str] | tuple[None, None]:
        if platform not in ("douyin", "douyu"):
            log.info("[START] danmaku not supported for platform=%s", platform)
            return None, None
        if getattr(sys, 'frozen', False):
            if os.path.isfile(_DANMAKU_GO_EXE):
                return [_DANMAKU_GO_EXE, platform, room_id], None
        else:
            if os.path.isdir(_DANMAKU_GO_DIR):
                log.info("[START] running %s worker via go run", platform)
                return ["go", "run", ".", platform, room_id], _DANMAKU_GO_DIR
        log.error("[START] danmaku worker not found (compile danmaku_go first)")
        self.error_occurred.emit("弹幕worker未编译, 请先安装Go并编译")
        return None, None

    def stop(self):
        self._running = False
        if self._process and self._process.poll() is None:
            log.info("[STOP] killing danmaku worker PID=%d", self._process.pid)
            if os.name == "nt":
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(self._process.pid)],
                        capture_output=True, timeout=5,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                except Exception:
                    self._process.kill()
            else:
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
                    log.debug("%s", line)
        except (ValueError, OSError):
            pass
