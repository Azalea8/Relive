"""Background worker threads for stream fetching, export, and danmaku rendering."""
import os
import subprocess
import tempfile

from PyQt6.QtCore import QThread, pyqtSignal
from src import config
from src.logger import get as _log
from src.recording import PLATFORMS
from src.danmaku import render_with_fallback, get_video_duration

_worker_log = _log("export")


class StreamWorker(QThread):
    """Fetch stream URL in background."""
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, room_id: str, platform: str = "douyu"):
        super().__init__()
        self.room_id = room_id
        self.platform = platform

    def run(self):
        try:
            url = PLATFORMS[self.platform].get_stream_url(self.room_id)
            self.finished.emit(url or "")
        except Exception as e:
            self.error.emit(str(e))


class ExportWorker(QThread):
    """Concat selected segments via FFmpeg stream copy."""
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, segment_paths: list[str], output_path: str,
                 log_path: str | None = None):
        super().__init__()
        self._paths = segment_paths
        self._output = output_path
        self._log_path = log_path

    def _log(self, msg: str):
        if not self._log_path:
            return
        try:
            from datetime import datetime
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(f"{ts}  {msg}\n")
        except OSError:
            pass

    def run(self):
        if not self._paths:
            self.error.emit("没有可导出的片段")
            return

        import time as _time
        t0 = _time.monotonic()

        list_fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="relive_concat_")
        try:
            with os.fdopen(list_fd, "w", encoding="utf-8") as f:
                for p in self._paths:
                    safe = p.replace("\\", "/").replace("'", "'\\''")
                    f.write(f"file '{safe}'\n")

            cmd = [
                config.FFMPEG_PATH, "-y",
                "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-c", "copy",
                self._output,
            ]
            _worker_log.info("export: %s", " ".join(cmd))

            self._log(f"[CONCAT] 开始拼接 {len(self._paths)} 个片段")
            self._log(f"[CONCAT] {' '.join(cmd)}")

            result = subprocess.run(
                cmd, capture_output=True, timeout=300,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            elapsed = _time.monotonic() - t0

            # Write all stderr to log
            if result.stderr:
                for line in result.stderr.decode("utf-8", errors="replace").splitlines():
                    if line.strip():
                        self._log(f"  {line.strip()}")

            if result.returncode != 0:
                err = result.stderr.decode("utf-8", errors="ignore")
                self._log(f"[CONCAT] 失败 exit={result.returncode} 耗时 {elapsed:.1f}s")
                _worker_log.error("export failed (exit=%d):\n%s", result.returncode, err)
                self.error.emit(f"FFmpeg failed: {err[-500:]}")
                return

            self._log(f"[CONCAT] 完成 exit=0 耗时 {elapsed:.1f}s")
            if result.stderr:
                _worker_log.info("export stderr:\n%s",
                                result.stderr.decode("utf-8", errors="ignore")[-2000:])

            self.finished.emit(self._output)

        except subprocess.TimeoutExpired:
            self._log("[CONCAT] 超时")
            self.error.emit("导出超时")
        except Exception as e:
            self._log(f"[CONCAT] 异常: {e}")
            self.error.emit(str(e))
        finally:
            try:
                os.remove(list_path)
            except OSError:
                pass


class RenderWorker(QThread):
    """Burn ASS subtitles into exported video via FFmpeg subtitles filter."""
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    progress = pyqtSignal(int, str)  # percentage (0-100), status text (e.g. "1.5x")

    def __init__(self, video_path: str, ass_path: str, output_path: str,
                 log_path: str | None = None):
        super().__init__()
        self._video = video_path
        self._ass = ass_path
        self._output = output_path
        self._log_path = log_path
        self._duration: float = 0.0
        self._last_emit_time: float = 0.0

    def run(self):
        import time as _time

        self._duration = get_video_duration(self._video)
        _worker_log.info(
            "[RENDER] video duration: %.1fs, output: %s",
            self._duration, self._output,
        )

        if self._log_path:
            from datetime import datetime
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(f"{ts}  [RENDER] 视频时长: {self._duration:.1f}s\n")
            except OSError:
                pass

        self._last_emit_time = 0.0

        def on_progress(time_sec: float, speed: float) -> bool:
            if self.isInterruptionRequested():
                return True

            now = _time.monotonic()
            if now - self._last_emit_time < (config.RENDER_PROGRESS_MS / 1000.0):
                return False
            self._last_emit_time = now

            if self._duration > 0:
                pct = min(int(time_sec / self._duration * 100), 99)
            else:
                pct = 0

            self.progress.emit(pct, f"{speed:.1f}x")
            return False

        def is_cancelled() -> bool:
            return self.isInterruptionRequested()

        try:
            ok = render_with_fallback(
                self._video, self._ass, self._output,
                on_progress=on_progress,
                is_cancelled=is_cancelled,
                log_path=self._log_path,
            )

            if ok:
                self.progress.emit(100, "完成")
                self.finished.emit(self._output)
            elif self.isInterruptionRequested():
                self._cleanup_output()
                self.error.emit("渲染已取消")
            else:
                self._cleanup_output()
                self.error.emit("所有编码器均失败")
        except Exception as e:
            self._cleanup_output()
            self.error.emit(f"渲染异常: {e}")

    def _cleanup_output(self):
        """Remove partial output file if it exists."""
        try:
            if os.path.exists(self._output):
                os.remove(self._output)
                _worker_log.info("[RENDER] cleaned up partial output: %s", self._output)
        except OSError:
            pass

    def cancel(self):
        """Request cancellation. The render loop will detect this and kill FFmpeg."""
        self.requestInterruption()
        _worker_log.info("[RENDER] cancel requested")
