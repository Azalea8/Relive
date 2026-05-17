"""mpv player wrapper — wid embedding into PyQt6 QWidget."""
import os

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QWidget

import mpv
from logger import get as _log


class VideoPlayer(QObject):
    _log = _log("mpv")
    position_changed = pyqtSignal(float)  # seconds
    duration_changed = pyqtSignal(float)  # seconds
    loaded = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, container: QWidget):
        super().__init__()
        self._container = container
        self._duration = 0.0
        self._last_position = -1.0
        self._player = None
        self._pending_seek: float | None = None
        self._seek_poll_count = 0
        self._expected_duration: float = 0.0
        self._seek_dur_stable_count = 0
        self._seek_last_dur: float | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(33)  # ~30fps
        self._create_mpv()

    def _create_mpv(self):
        """Create a fresh mpv instance embedded in the container widget."""
        wid = str(int(self._container.winId()))
        self._player = mpv.MPV(
            wid=wid,
            keep_open='yes',
            osc='no',
            input_default_bindings='no',
            input_vo_keyboard='no',
            ytdl='no',
            hwdec='auto',
            hwdec_codecs='all',
            hr_seek='yes',
            force_seekable='yes',
            log_handler=self._on_mpv_log,
        )
        self._log.info("mpv instance created")

    def reinitialize(self, source: str, start_pos: float | None = None,
                     expected_duration: float = 0.0, sub_file: str = ""):
        """Destroy current mpv and create a fresh one playing `source`.

        Args:
            source: URL or local file path to play.
            start_pos: If set, seek to this position once playback starts.
            expected_duration: Expected total duration — seek waits until
                mpv reports dur >= 80% of this before executing.
            sub_file: Optional ASS subtitle file to load.
        """
        self._log.info("[REINIT] source=%s start_pos=%s expected_dur=%.1f sub_file=%s",
                       source[:120], start_pos, expected_duration, sub_file)

        # Destroy old instance
        if self._player is not None:
            self._timer.stop()
            try:
                self._player.terminate()
            except Exception as e:
                self._log.error("[REINIT] terminate error: %s", e)

        # Reset state
        self._duration = 0.0
        self._last_position = -1.0
        self._seek_poll_count = 0
        self._expected_duration = expected_duration
        self._seek_dur_stable_count = 0
        self._seek_last_dur = None

        # Create new instance, then set pending_seek
        self._create_mpv()
        self._pending_seek = start_pos
        self._timer.start(33)

        # Load subtitle BEFORE play (matching StreamSlice behavior)
        if sub_file:
            self._player.sub_files = sub_file
            self._log.info("[REINIT] sub_files=%s OK", sub_file)

        # Play directly — source is either a URL or a pre-built snapshot m3u8
        is_url = source.startswith("http://") or source.startswith("https://")
        self._log.info("[REINIT] play(%s) is_url=%s", source[:120], is_url)
        self._player.play(source)

    def _on_mpv_log(self, loglevel, component, message):
        if loglevel in ("error", "fatal"):
            self._log.error("mpv [%s] %s", component or "core", message.strip())

    def _poll(self):
        if self._player is None:
            return
        try:
            pos = self._player.time_pos
            if pos is not None and pos != self._last_position:
                self._last_position = pos
                self.position_changed.emit(pos)

            dur = self._player.duration
            if dur is not None and dur != self._duration:
                self._duration = dur
                self.duration_changed.emit(dur)

            # Seek: wait until mpv has parsed enough of the m3u8.
            # Two conditions (whichever comes first):
            #   1. dur >= expected * 0.5  (reached enough of the playlist)
            #   2. dur stable for 3 polls  (mpv finished parsing)
            if self._pending_seek is not None:
                if dur is not None and dur > 0:
                    # Track stability
                    if self._seek_last_dur is not None and abs(dur - self._seek_last_dur) < 0.01:
                        self._seek_dur_stable_count += 1
                    else:
                        self._seek_dur_stable_count = 0
                    self._seek_last_dur = dur

                    threshold = max(self._expected_duration * 0.5, 2.0)
                    ready = dur >= threshold or self._seek_dur_stable_count >= 3

                    if ready:
                        self._log.info(
                            "[SEEK_POLL] ready: dur=%.3f threshold=%.3f stable=%d, seeking to %.3f",
                            dur, threshold, self._seek_dur_stable_count, self._pending_seek)
                        seek_to = self._pending_seek
                        self._pending_seek = None
                        self.seek(seek_to)
                        self.loaded.emit()
                    else:
                        self._seek_poll_count += 1
                        if self._seek_poll_count % 10 == 0:
                            self._log.info(
                                "[SEEK_POLL] waiting: dur=%.3f threshold=%.3f stable=%d poll=%d",
                                dur, threshold, self._seek_dur_stable_count, self._seek_poll_count)

        except mpv.ShutdownError:
            self._log.warning("[POLL] mpv ShutdownError, stopping timer")
            self._timer.stop()
        except Exception as e:
            self._log.error("[POLL] unexpected error: %s", e)

    # --- public API ---

    def play(self):
        if self._player:
            self._player.pause = False

    def pause(self):
        if self._player:
            self._player.pause = True

    def seek(self, seconds: float):
        if not self._player:
            return
        self._log.info("seek(%.3f)", seconds)
        try:
            self._player.seek(seconds, reference='absolute')
        except Exception as e:
            self._log.error("seek FAILED: %s", e)

    def set_speed(self, speed: float):
        if self._player:
            self._player.speed = speed

    def set_sub_file(self, path: str):
        """Load a subtitle file immediately."""
        if self._player:
            try:
                self._player.sub_files = path
                self._log.info("set sub_files=%s OK", path)
            except Exception as e:
                self._log.error("set sub_files failed: %s", e)

    def sub_reload(self):
        if self._player:
            try:
                self._player.command("sub-reload", "1")
            except Exception:
                pass

    def speed(self) -> float:
        return self._player.speed if self._player else 1.0

    def position(self) -> float:
        return (self._player.time_pos or 0.0) if self._player else 0.0

    def duration(self) -> float:
        return (self._player.duration or 0.0) if self._player else 0.0

    def close(self):
        self._timer.stop()
        if self._player:
            try:
                self._player.terminate()
            except Exception:
                pass
            self._player = None
