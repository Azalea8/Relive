"""Background worker threads for stream fetching, export, and danmaku rendering."""
import os
import subprocess
import tempfile

from PyQt6.QtCore import QThread, pyqtSignal
from src import config
from src.logger import get as _log
from src.recording import douyu_stream_url
from src.recording import huya_stream_url
from src.danmaku import render_with_fallback

_worker_log = _log("export")


class StreamWorker(QThread):
    """Fetch stream URL in background."""
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, room_id: str, quality: str, platform: str = "douyu"):
        super().__init__()
        self.room_id = room_id
        self.quality = quality
        self.platform = platform

    def run(self):
        try:
            if self.platform == "huya":
                url = huya_stream_url.get_stream_url(self.room_id, self.quality)
            else:
                url = douyu_stream_url.get_stream_url(self.room_id, self.quality)
            self.finished.emit(url or "")
        except Exception as e:
            self.error.emit(str(e))


class ExportWorker(QThread):
    """Concat selected segments via FFmpeg stream copy."""
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, segment_paths: list[str], output_path: str):
        super().__init__()
        self._paths = segment_paths
        self._output = output_path

    def run(self):
        if not self._paths:
            self.error.emit("没有可导出的片段")
            return

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

            result = subprocess.run(
                cmd, capture_output=True, timeout=300,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            if result.returncode != 0:
                err = result.stderr.decode("utf-8", errors="ignore")
                _worker_log.error("export failed (exit=%d):\n%s", result.returncode, err)
                self.error.emit(f"FFmpeg failed: {err[-500:]}")
                return
            if result.stderr:
                _worker_log.info("export stderr:\n%s",
                                result.stderr.decode("utf-8", errors="ignore")[-2000:])

            self.finished.emit(self._output)

        except subprocess.TimeoutExpired:
            self.error.emit("导出超时")
        except Exception as e:
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

    def __init__(self, video_path: str, ass_path: str, output_path: str):
        super().__init__()
        self._video = video_path
        self._ass = ass_path
        self._output = output_path

    def run(self):
        try:
            ok = render_with_fallback(self._video, self._ass, self._output)
            if ok:
                self.finished.emit(self._output)
            else:
                self.error.emit("所有编码器均失败")
        except Exception as e:
            self.error.emit(f"渲染异常: {e}")
