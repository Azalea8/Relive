"""Cache manager — reads FFmpeg's HLS m3u8 to track segments for DVR."""
import os
import tempfile

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from src.logger import get as _log
from src import config

log = _log("cache")

SNAPSHOT_M3U8 = os.path.join(config.SEGMENT_DIR, "snapshot.m3u8")


class Segment:
    __slots__ = ("filename", "path", "index", "duration")

    def __init__(self, filename: str, path: str, index: int, duration: float):
        self.filename = filename
        self.path = path
        self.index = index
        self.duration = duration


class CacheManager(QObject):
    """Reads FFmpeg's live HLS m3u8, tracks segments, generates VOD snapshot."""

    segments_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.segments: list[Segment] = []
        self._last_segment_count = -1
        self._last_m3u8_size: int = 0
        self._cached_total: float = 0.0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._scan)
        self._timer.start(config.CACHE_CHECK_MS)

    def total_duration(self) -> float:
        return self._cached_total

    def find_segment_at(self, offset_sec: float) -> tuple[int, float] | None:
        elapsed = 0.0
        for i, seg in enumerate(self.segments):
            if elapsed + seg.duration > offset_sec:
                return (i, offset_sec - elapsed)
            elapsed += seg.duration
        return None

    def get_absolute_time(self, segment_index: int, offset_in_segment: float) -> float:
        total = 0.0
        for i, seg in enumerate(self.segments):
            if i == segment_index:
                return total + offset_in_segment
            total += seg.duration
        return total

    def write_snapshot(self) -> tuple[str, float]:
        """Generate a frozen VOD m3u8 from current segment list."""
        segs = self.segments
        if not segs:
            return "", 0.0

        max_dur = max(s.duration for s in segs)
        target_dur = max(int(max_dur) + 1, 1)

        lines = [
            "#EXTM3U\n",
            "#EXT-X-VERSION:3\n",
            f"#EXT-X-TARGETDURATION:{target_dur}\n",
            "#EXT-X-MEDIA-SEQUENCE:0\n",
            "#EXT-X-PLAYLIST-TYPE:VOD\n",
        ]
        for seg in segs:
            lines.append(f"#EXTINF:{seg.duration:.6f},\n")
            lines.append(f"{seg.filename}\n")
        lines.append("#EXT-X-ENDLIST\n")

        fd, tmp = tempfile.mkstemp(suffix=".tmp", dir=config.SEGMENT_DIR)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.writelines(lines)
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

        total = self._cached_total
        log.info("[SNAPSHOT] wrote %d segments, %.1fs", len(segs), total)
        return SNAPSHOT_M3U8, total

    def _scan(self):
        try:
            self._parse_m3u8()
        except Exception as e:
            log.error("[SCAN] error: %s", e)

    def _parse_m3u8(self):
        """Parse FFmpeg's live HLS m3u8 for segment list."""
        m3u8_path = config.M3U8_PATH
        if not os.path.exists(m3u8_path):
            return

        # Fast-path: skip if file size unchanged
        try:
            size = os.path.getsize(m3u8_path)
        except OSError:
            return
        if size == self._last_m3u8_size:
            return
        self._last_m3u8_size = size

        try:
            with open(m3u8_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return

        segments: list[Segment] = []
        current_dur = 0.0
        seg_index = 0

        for line in lines:
            line = line.strip()
            if line.startswith("#EXTINF:"):
                try:
                    current_dur = float(line[8:].rstrip(","))
                except ValueError:
                    current_dur = float(config.SEGMENT_SEC)
            elif line and not line.startswith("#"):
                # Segment URI — relative to m3u8 in videos/
                filename = line
                path = os.path.normpath(os.path.join(config.SEGMENT_DIR, filename))
                segments.append(Segment(
                    filename=filename,
                    path=path,
                    index=seg_index,
                    duration=current_dur,
                ))
                seg_index += 1
                current_dur = 0.0

        # Skip if nothing changed
        if len(segments) == len(self.segments):
            if all(
                s1.filename == s2.filename and s1.duration == s2.duration
                for s1, s2 in zip(segments, self.segments)
            ):
                return

        self._cached_total = sum(s.duration for s in segments)
        self.segments = segments
        if len(self.segments) != self._last_segment_count:
            self._last_segment_count = len(self.segments)
        log.info("[SCAN] %d segments, total=%.1fs", len(segments), self._cached_total)
        self.segments_changed.emit()
