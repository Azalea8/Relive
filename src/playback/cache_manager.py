"""Cache manager — reads FFmpeg's HLS m3u8 to track segments for DVR."""
import os
import tempfile
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from src.logger import get as _log
from src import config

log = _log("cache")

SNAPSHOT_M3U8 = os.path.join(config.SEGMENT_DIR, "snapshot.m3u8")


class CacheManager(QObject):
    """Tracks segment count / total duration / time bounds from FFmpeg's HLS m3u8."""

    segments_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._count: int = 0
        self._cached_total: float = 0.0
        self._first_ts: str = ""
        self._last_ts: str = ""
        self._last_cleanup: float = 0.0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._scan)
        self._timer.start(config.CACHE_CHECK_MS)

    def total_duration(self) -> float:
        return self._cached_total

    @property
    def segment_count(self) -> int:
        return self._count

    @property
    def first_ts(self) -> str:
        return self._first_ts

    @property
    def last_ts(self) -> str:
        return self._last_ts

    def get_paths_in_range(self, start_sec: float, end_sec: float) -> list[str]:
        """Return absolute paths of TS files overlapping [start_sec, end_sec).
        Parses the m3u8 on demand — only called during export, not on the hot path."""
        m3u8_path = config.M3U8_PATH
        if not os.path.exists(m3u8_path):
            return []
        try:
            with open(m3u8_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return []

        paths: list[str] = []
        elapsed = 0.0
        current_dur = 0.0
        for line in lines:
            line = line.strip()
            if line.startswith("#EXTINF:"):
                try:
                    current_dur = float(line[8:].rstrip(","))
                except ValueError:
                    current_dur = float(config.SEGMENT_SEC)
            elif line and not line.startswith("#"):
                seg_end = elapsed + current_dur
                if seg_end > start_sec and elapsed < end_sec:
                    path = os.path.normpath(os.path.join(config.SEGMENT_DIR, line))
                    paths.append(path)
                elapsed = seg_end
                current_dur = 0.0
        return paths

    def write_snapshot(self) -> tuple[str, float]:
        """Clone FFmpeg's live m3u8 into a VOD snapshot with #EXT-X-ENDLIST."""
        m3u8_path = config.M3U8_PATH
        if not os.path.exists(m3u8_path):
            return "", 0.0

        try:
            with open(m3u8_path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            return "", 0.0

        if not content.strip():
            return "", 0.0

        lines = content.rstrip().split("\n")
        out: list[str] = []
        version_seen = False
        for line in lines:
            stripped = line.strip()
            out.append(stripped)
            if not version_seen and stripped.startswith("#EXT-X-VERSION:"):
                out.append("#EXT-X-PLAYLIST-TYPE:VOD")
                version_seen = True

        out = [l for l in out if l != "#EXT-X-ENDLIST"]
        out.append("#EXT-X-ENDLIST")

        fd, tmp = tempfile.mkstemp(suffix=".tmp", dir=config.SEGMENT_DIR)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(out))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, SNAPSHOT_M3U8)
        except OSError as e:
            log.error("[SNAPSHOT] write failed: %s", e)
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return "", 0.0

        log.info("[SNAPSHOT] wrote %d segments, %.1fs", self._count, self._cached_total)
        return SNAPSHOT_M3U8, self._cached_total

    def _scan(self):
        try:
            self._scan_m3u8()
        except Exception as e:
            log.error("[SCAN] error: %s", e)

    def _scan_m3u8(self):
        """Extract segment count / total duration / time bounds from m3u8."""
        m3u8_path = config.M3U8_PATH
        if not os.path.exists(m3u8_path):
            return

        try:
            with open(m3u8_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return

        count = 0
        total = 0.0
        first_ts = ""
        last_ts = ""
        current_dur = 0.0

        for line in lines:
            line = line.strip()
            if line.startswith("#EXTINF:"):
                try:
                    current_dur = float(line[8:].rstrip(","))
                except ValueError:
                    current_dur = float(config.SEGMENT_SEC)
            elif line and not line.startswith("#"):
                if not first_ts:
                    first_ts = line
                last_ts = line
                total += current_dur
                count += 1
                current_dur = 0.0

        if count == 0:
            return

        changed = (count != self._count
                   or abs(total - self._cached_total) > 0.5
                   or first_ts != self._first_ts)

        self._count = count
        self._cached_total = total
        self._first_ts = first_ts
        self._last_ts = last_ts

        # Delete TS files older than the playlist window
        now = time.time()
        if now - self._last_cleanup >= config.TS_CLEANUP_HOURS * 3600 and first_ts:
            orphaned: list[str] = []
            try:
                for f in sorted(os.listdir(config.SEGMENT_DIR)):
                    if not f.endswith('.ts'):
                        continue
                    if f >= first_ts:
                        break
                    try:
                        os.remove(os.path.join(config.SEGMENT_DIR, f))
                        orphaned.append(f)
                    except OSError:
                        pass
            except OSError:
                pass
            if orphaned:
                msg = f"[CLEANUP] removed {len(orphaned)} orphan TS: {orphaned[0]} .. {orphaned[-1]}"
                log.info(msg)
                print(msg)
            self._last_cleanup = now

        if changed:
            log.info("[SCAN] %d segments, total=%.1fs", count, total)
            self.segments_changed.emit()
