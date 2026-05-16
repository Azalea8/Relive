"""Cache manager — parses m3u8 for segment list, tracks DVR window."""
import os
import tempfile
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from logger import get as _log
import config

log = _log("cache")

SNAPSHOT_M3U8 = os.path.join(config.CACHE_DIR, "snapshot.m3u8")


class Segment:
    __slots__ = ("filename", "path", "index", "duration")

    def __init__(self, filename: str, path: str, index: int, duration: float):
        self.filename = filename
        self.path = path
        self.index = index  # position in m3u8
        self.duration = duration


class CacheManager(QObject):
    """Parses FFmpeg's m3u8 to track available segments for DVR."""

    segments_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.segments: list[Segment] = []
        self._last_segment_count = -1

        # Periodic m3u8 parse timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._scan)
        self._timer.start(config.CACHE_CHECK_MS)

    def total_duration(self) -> float:
        """Total cache duration in seconds."""
        return sum(s.duration for s in self.segments)

    def find_segment_at(self, offset_sec: float) -> tuple[int, float] | None:
        """Find which segment contains the given offset (seconds from cache start).
        Returns (segment_index, offset_within_segment) or None."""
        elapsed = 0.0
        for i, seg in enumerate(self.segments):
            if elapsed + seg.duration > offset_sec:
                result = (i, offset_sec - elapsed)
                log.info("[FIND_SEG] offset=%.1f -> seg_idx=%d offset_in=%.1f (seg_dur=%.3f elapsed=%.1f)",
                         offset_sec, result[0], result[1], seg.duration, elapsed)
                return result
            elapsed += seg.duration
        log.warning("[FIND_SEG] offset=%.1f not found in %d segments (total=%.1f)",
                    offset_sec, len(self.segments), elapsed)
        return None

    def get_absolute_time(self, segment_index: int, offset_in_segment: float) -> float:
        """Convert segment index + offset to absolute offset from cache start."""
        total = 0.0
        for i, seg in enumerate(self.segments):
            if i == segment_index:
                result = total + offset_in_segment
                log.info("[ABS_TIME] seg_idx=%d offset=%.1f -> abs=%.1f",
                         segment_index, offset_in_segment, result)
                return result
            total += seg.duration
        log.warning("[ABS_TIME] seg_idx=%d not found, returning total=%.1f", segment_index, total)
        return total

    def write_snapshot(self) -> tuple[str, float]:
        """Generate a frozen snapshot.m3u8 from current segment list.

        The snapshot is a complete VOD playlist — mpv treats it as a normal
        video file, not a live stream.  Only rebuilt on user seek, never
        during playback.
        Returns (path, total_duration).
        """
        segs = self.segments
        if not segs:
            return "", 0.0

        max_dur = max(s.duration for s in segs)
        target_dur = max(int(max_dur) + 1, 1)
        total = self.total_duration()

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

        fd, tmp = tempfile.mkstemp(suffix=".tmp", dir=config.CACHE_DIR)
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

        log.info("[SNAPSHOT] wrote %d segments, %.1fs, target_dur=%d",
                 len(segs), total, target_dur)
        return SNAPSHOT_M3U8, total

    def _scan(self):
        """Parse m3u8 for updated segment list."""
        try:
            self._parse_m3u8()
        except Exception as e:
            log.error("m3u8 parse error: %s", e)

    def _parse_m3u8(self):
        """Parse FFmpeg's m3u8 file for segment info."""
        m3u8_path = config.M3U8_PATH
        if not os.path.exists(m3u8_path):
            return

        try:
            with open(m3u8_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return

        segments: list[Segment] = []
        current_duration = 0.0
        seg_index = 0

        for line in lines:
            line = line.strip()
            if line.startswith("#EXTINF:"):
                try:
                    current_duration = float(line[8:].rstrip(","))
                except ValueError:
                    current_duration = float(config.SEGMENT_SEC)
            elif line and not line.startswith("#"):
                # This is a segment filename
                filename = line
                path = os.path.join(config.CACHE_DIR, filename)
                if os.path.exists(path):
                    segments.append(Segment(
                        filename=filename,
                        path=path,
                        index=seg_index,
                        duration=current_duration,
                    ))
                    seg_index += 1
                current_duration = 0.0

        self.segments = segments
        if len(self.segments) != self._last_segment_count:
            self._last_segment_count = len(self.segments)
            total = self.total_duration()
            log.info("[PARSE] m3u8: %d segments, total=%.1fs", len(self.segments), total)
            for i, seg in enumerate(self.segments):
                log.debug("[PARSE]   seg[%d] %s dur=%.3f", i, seg.filename, seg.duration)
            self.segments_changed.emit()
