"""FFmpeg recorder — segment muxer, TS files only. m3u8 maintained by Python."""
import atexit
import os
import signal
import subprocess
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal
from src.logger import get as _log
from src import config

log = _log("recorder")

_live_pids: set[int] = set()


def _cleanup_all():
    for pid in _live_pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    _live_pids.clear()


atexit.register(_cleanup_all)

from src.winjob import JobObject

_winjob = JobObject()


class FFmpegRecorder(QObject):
    """Manages an FFmpeg subprocess that captures a live stream into TS segments."""

    state_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process: subprocess.Popen | None = None
        self._stream_url: str = ""
        self._start_time: float = 0.0
        self._running = False
        self._last_seglist_mtime: float = 0.0
        self._last_mtime_check: float = 0.0

        self._health_timer = QTimer(self)
        self._health_timer.timeout.connect(self._check_health)
        self._health_timer.start(5000)

    def start(self, stream_url: str, http_headers: str = ""):
        if self._running:
            log.info("[START] stopping existing FFmpeg first")
            self.stop()

        self._stream_url = stream_url
        os.makedirs(config.SEGMENT_DIR, exist_ok=True)

        clean_url = stream_url.replace(":443", "") if ":443" in stream_url else stream_url

        # Session prefix + segment index avoids duplicate filenames across
        # reconnects (strftime at second granularity collides with hls_time=4).
        prefix = time.strftime("%Y%m%d_%H%M%S")
        output_pattern = os.path.join(config.SEGMENT_DIR, f"{prefix}_%06d.ts")

        cmd = [
            config.FFMPEG_PATH,
            "-loglevel", "warning",
            "-y",
            "-fflags", "+genpts",
        ]
        if http_headers:
            cmd += ["-headers", http_headers]
        cmd += [
            "-i", clean_url,
            "-sn",          # skip subtitle tracks (Huya FLV carries WebVTT)
            "-c", "copy",
            "-f", "hls",
            "-hls_time", str(config.SEGMENT_SEC),
            "-hls_segment_type", "mpegts",
            "-hls_flags", "append_list",
            "-hls_list_size", str((config.CACHE_HOURS * 3600) // config.SEGMENT_SEC),
            "-hls_segment_filename", output_pattern,
            config.M3U8_PATH,
        ]

        log.info("[START] ffmpeg_path=%s exists=%s", config.FFMPEG_PATH, os.path.exists(config.FFMPEG_PATH))
        log.info("[START] cmd: %s", " ".join(cmd))
        log.info("[START] stream_url=%s", stream_url[:120])

        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW

        env = os.environ.copy()
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            val = os.environ.get(key)
            if val:
                env.setdefault(key, val)
                log.info("[START] proxy env: %s=%s", key, val)

        self._stderr_path = os.path.join(config.CACHE_DIR, "ffmpeg_stderr.log")
        self._stderr_lines: list[str] = []
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=creationflags,
                env=env,
            )
        except Exception as e:
            log.error("[START] Popen FAILED: %s", e)
            self._running = False
            self.state_changed.emit("error")
            return

        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()
        _live_pids.add(self._process.pid)

        try:
            _winjob.add_pid(self._process.pid)
            log.info("[START] FFmpeg added to JobObject")
        except Exception as e:
            log.warning("[START] JobObject.add_pid failed: %s", e)

        self._start_time = time.time()
        self._last_seglist_mtime = self._get_seglist_mtime()
        self._last_mtime_check = time.time()
        self._running = True

        log.info("[START] FFmpeg segment started, PID=%d", self._process.pid)
        log.info(f"[START] FFmpeg _start_time, {self._start_time}")
        self.state_changed.emit("recording")

    def stop(self):
        if not self._process or self._process.poll() is not None:
            exit_code = self._process.returncode if self._process else "N/A"
            log.info("[STOP] FFmpeg already dead or None, exit_code=%s", exit_code)
            self._running = False
            _live_pids.discard(self._process.pid if self._process else 0)
            self._close_stderr()
            return

        log.info("[STOP] sending 'q' to FFmpeg PID=%d, uptime=%.1fs",
                 self._process.pid, time.time() - self._start_time)
        try:
            self._process.stdin.write(b"q\n")
            self._process.stdin.flush()
        except (OSError, BrokenPipeError):
            pass

        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.warning("[STOP] FFmpeg did not exit in 5s, killing")
            _kill_process_tree(self._process)
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass

        if hasattr(self, '_stderr_thread') and self._stderr_thread.is_alive():
            self._stderr_thread.join(timeout=2.0)

        _live_pids.discard(self._process.pid)
        self._running = False
        self._close_stderr()
        self.state_changed.emit("stopped")

    def is_running(self) -> bool:
        return self._running and self._process is not None and self._process.poll() is None

    @property
    def start_time(self) -> float:
        return self._start_time

    @property
    def elapsed(self) -> float:
        if not self._running:
            return 0.0
        return time.time() - self._start_time

    def _get_seglist_mtime(self) -> float:
        try:
            return os.path.getmtime(config.M3U8_PATH)
        except OSError:
            return 0.0

    def _drain_stderr(self):
        try:
            for raw_line in self._process.stderr:
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                self._stderr_lines.append(line)
                log.debug("[STDERR] %s", line)
                if len(self._stderr_lines) > 200:
                    self._stderr_lines = self._stderr_lines[-100:]
        except (ValueError, OSError) as e:
            log.info("[STDERR] drain ended: %s", e)

    def _flush_stderr_log(self):
        try:
            with open(self._stderr_path, "w", encoding="utf-8") as f:
                f.write("\n".join(self._stderr_lines))
        except OSError:
            pass

    def _close_stderr(self):
        try:
            self._flush_stderr_log()
        except Exception:
            pass

    def _check_health(self):
        if not self._running:
            return

        now = time.time()
        uptime = now - self._start_time

        poll_result = self._process.poll() if self._process else None
        if self._process and poll_result is not None:
            self._stderr_thread.join(timeout=1.0)
            tail = self._stderr_lines[-10:] if self._stderr_lines else ["(no stderr)"]
            log.error("[HEALTH] FFmpeg DIED: exit_code=%s uptime=%.1fs PID=%d",
                      poll_result, uptime, self._process.pid)
            log.error("[HEALTH] Last stderr (%d lines total):\n  %s",
                      len(self._stderr_lines), "\n  ".join(tail))
            _live_pids.discard(self._process.pid)
            self._close_stderr()
            self._running = False
            self.state_changed.emit("error")
            return

        current_mtime = self._get_seglist_mtime()
        time_since_change = now - self._last_mtime_check

        if current_mtime == self._last_seglist_mtime:
            if time_since_change >= config.STALL_TIMEOUT:
                log.warning("[HEALTH] FFmpeg STALLED: m3u8 unchanged for %.1fs", time_since_change)
                tail = self._stderr_lines[-5:] if self._stderr_lines else ["(no stderr)"]
                log.warning("[HEALTH] stderr tail:\n  %s", "\n  ".join(tail))
                _kill_process_tree(self._process)
                _live_pids.discard(self._process.pid)
                self._close_stderr()
                self._running = False
                self.state_changed.emit("error")
        else:
            log.debug("[HEALTH] m3u8 updated: %.3f -> %.3f", self._last_seglist_mtime, current_mtime)
            self._last_seglist_mtime = current_mtime
            self._last_mtime_check = now


def _kill_process_tree(proc: subprocess.Popen):
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception:
            proc.kill()
    else:
        proc.kill()
