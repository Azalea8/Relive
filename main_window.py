"""ReLive main window — live stream DVR with mpv playback."""
import json
import os
import time

from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QStyle,
    QStyleOptionSlider,
    QVBoxLayout,
    QWidget,
)

from video_player import VideoPlayer
from ffmpeg_recorder import FFmpegRecorder
from cache_manager import CacheManager
import stream_url
from logger import get as _log
import config

log = _log("ui")


# ---------------------------------------------------------------------------
# SeekSlider — DVR timeline
# ---------------------------------------------------------------------------

class SeekSlider(QSlider):
    """Slider with click-to-seek + mark-in/out lines."""

    _log = _log("slider")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mark_in_pos: int | None = None
        self._mark_out_pos: int | None = None

    def set_mark_in_line(self, pos: int | None):
        self._mark_in_pos = pos
        self.update()

    def set_mark_out_line(self, pos: int | None):
        self._mark_out_pos = pos
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            val = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), int(event.position().x()), self.width()
            )
            self._log.info("click: x=%d -> val=%d (range %d-%d)",
                           int(event.position().x()), val, self.minimum(), self.maximum())
            self.setValue(val)
            self.sliderPressed.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.maximum() <= 0:
            return

        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, opt,
            QStyle.SubControl.SC_SliderGroove, self,
        )
        total = self.maximum()
        groove_x = groove.x()
        groove_w = groove.width()
        groove_y = groove.y()
        groove_h = groove.height()

        painter = QPainter(self)
        region_y = groove_y - 5
        region_h = groove_h + 10

        # Mark-in line (orange)
        if self._mark_in_pos is not None and self._mark_in_pos >= 0:
            x = groove_x + int(self._mark_in_pos / total * groove_w)
            painter.setPen(QColor(234, 146, 90, 220))
            painter.drawLine(x, region_y, x, region_y + region_h)
            painter.drawLine(x + 1, region_y, x + 1, region_y + region_h)

        # Mark-out line (purple)
        if self._mark_out_pos is not None and self._mark_out_pos >= 0:
            x = groove_x + int(self._mark_out_pos / total * groove_w)
            painter.setPen(QColor(124, 58, 237, 220))
            painter.drawLine(x, region_y, x, region_y + region_h)
            painter.drawLine(x + 1, region_y, x + 1, region_y + region_h)

        painter.end()


# ---------------------------------------------------------------------------
# Stream worker — runs network calls off the UI thread
# ---------------------------------------------------------------------------

class _StreamWorker(QThread):
    """Fetch stream URL in background."""
    finished = pyqtSignal(str)  # stream URL or empty string
    error = pyqtSignal(str)

    def __init__(self, room_id: str, quality: str):
        super().__init__()
        self.room_id = room_id
        self.quality = quality

    def run(self):
        try:
            url = stream_url.get_stream_url(self.room_id, self.quality)
            self.finished.emit(url or "")
        except Exception as e:
            self.error.emit(str(e))


# ---------------------------------------------------------------------------
# Export worker
# ---------------------------------------------------------------------------

class _ExportWorker(QThread):
    """Concat selected segments via FFmpeg stream copy."""
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(str)  # output path
    error = pyqtSignal(str)

    def __init__(self, segment_paths: list[str], output_path: str):
        super().__init__()
        self._paths = segment_paths
        self._output = output_path

    def run(self):
        import subprocess
        import tempfile

        if not self._paths:
            self.error.emit("没有可导出的片段")
            return

        # Write concat list
        list_fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="relive_concat_")
        try:
            with os.fdopen(list_fd, "w", encoding="utf-8") as f:
                for p in self._paths:
                    # Normalize path for FFmpeg on Windows
                    safe = p.replace("\\", "/").replace("'", "'\\''")
                    f.write(f"file '{safe}'\n")

            cmd = [
                config.FFMPEG_PATH, "-y",
                "-f", "concat", "-safe", "0",
                "-i", list_path,
                "-c", "copy",
                self._output,
            ]
            log.info("export: %s", " ".join(cmd))

            result = subprocess.run(
                cmd, capture_output=True, timeout=300,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            if result.returncode != 0:
                err = result.stderr.decode("utf-8", errors="ignore")[-500:]
                self.error.emit(f"FFmpeg failed: {err}")
                return

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


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #1a1b26;
    color: #c0caf5;
    font-family: "Segoe UI", sans-serif;
    font-size: 13px;
}

QSlider::groove:horizontal {
    background: #2f3140;
    height: 6px;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #7c3aed;
    width: 10px;
    height: 10px;
    margin: -2px 0;
    border-radius: 5px;
}
QSlider::handle:horizontal:hover {
    background: #8b5cf6;
}
QSlider::sub-page:horizontal {
    background: #7c3aed;
    border-radius: 3px;
}

QPushButton {
    background-color: #2f3140;
    color: #c0caf5;
    border: 1px solid #3b3d54;
    border-radius: 6px;
    padding: 4px 10px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #3b3d54;
    border-color: #7c3aed;
}
QPushButton:pressed {
    background-color: #7c3aed;
    color: #ffffff;
}
QPushButton:disabled {
    background-color: #24253a;
    color: #565f89;
    border-color: #2f3140;
}

QPushButton#btn_connect {
    background-color: #7c3aed;
    color: #ffffff;
    border: none;
    font-weight: 600;
    padding: 6px 16px;
}
QPushButton#btn_connect:hover {
    background-color: #8b5cf6;
}

QPushButton#btn_live {
    background-color: #059669;
    color: #ffffff;
    border: none;
    font-weight: 600;
}
QPushButton#btn_live:hover {
    background-color: #10b981;
}

QPushButton#btn_mark_in {
    background-color: #c2410c;
    color: #ffffff;
    border: none;
    font-weight: 600;
}
QPushButton#btn_mark_in:hover {
    background-color: #ea925a;
}
QPushButton#btn_mark_out {
    background-color: #7c3aed;
    color: #ffffff;
    border: none;
    font-weight: 600;
}
QPushButton#btn_mark_out:hover {
    background-color: #9461f7;
}

QPushButton#btn_export {
    background-color: #0ea5e9;
    color: #ffffff;
    border: none;
    font-weight: 600;
}
QPushButton#btn_export:hover {
    background-color: #38bdf8;
}
QPushButton#btn_export:disabled {
    background-color: #3b3d54;
    color: #565f89;
}

QComboBox {
    background-color: #2f3140;
    color: #c0caf5;
    border: 1px solid #3b3d54;
    border-radius: 4px;
    padding: 4px 8px;
}

QLineEdit {
    background-color: #2f3140;
    color: #c0caf5;
    border: 1px solid #3b3d54;
    border-radius: 4px;
    padding: 4px 8px;
}
QLineEdit:focus {
    border-color: #7c3aed;
}

QStatusBar {
    background-color: #161723;
    color: #9aa5ce;
    border-top: 1px solid #2f3140;
    padding: 2px 8px;
    font-size: 12px;
}

QLabel {
    color: #a9b1d6;
    background: transparent;
}
"""


def _fmt_time(seconds: float) -> str:
    """Format seconds to MM:SS or HH:MM:SS."""
    if seconds < 0:
        seconds = 0
    total_s = int(seconds)
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ReLive")
        self.resize(1120, 680)
        self.setStyleSheet(_STYLESHEET)

        # State
        self._room_id = ""
        self._stream_url: str = ""
        self._is_live_mode = True
        self._connected = False
        self._user_interacting_slider = False
        self._mark_in_sec: float | None = None
        self._mark_out_sec: float | None = None
        self._seeking_from_slider = False
        self._dvr_frozen_duration = 0.0
        self._stream_worker: _StreamWorker | None = None
        self._export_worker: _ExportWorker | None = None
        self._reconnect_count = 0

        # --- central widget ---
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # === Top bar: connection controls ===
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        top_row.addWidget(QLabel("房间号:"))
        self._room_input = QWidget()
        room_layout = QHBoxLayout(self._room_input)
        room_layout.setContentsMargins(0, 0, 0, 0)
        room_layout.setSpacing(4)
        self._room_combo = QComboBox()
        self._room_combo.setEditable(True)
        self._room_combo.setPlaceholderText("斗鱼房间号")
        self._room_combo.setMinimumWidth(160)
        self._room_combo.lineEdit().returnPressed.connect(self._on_connect)
        room_layout.addWidget(self._room_combo)

        self._quality_combo = QComboBox()
        self._quality_combo.addItems(["原画", "高清", "流畅"])
        self._quality_combo.setCurrentIndex(0)
        self._quality_combo.setMinimumWidth(80)
        room_layout.addWidget(self._quality_combo)

        self._btn_connect = QPushButton("连接")
        self._btn_connect.setObjectName("btn_connect")
        self._btn_connect.clicked.connect(self._on_connect)
        room_layout.addWidget(self._btn_connect)

        top_row.addWidget(self._room_input)
        top_row.addStretch()

        self._status_label = QLabel("未连接")
        self._status_label.setStyleSheet("color: #565f89; font-size: 12px;")
        top_row.addWidget(self._status_label)

        layout.addLayout(top_row)

        # === Video area ===
        self._video_widget = QWidget()
        self._video_widget.setStyleSheet("background-color: black")
        self._video_widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._video_widget.installEventFilter(self)
        layout.addWidget(self._video_widget, stretch=1)

        # Player
        self._player = VideoPlayer(self._video_widget)

        # === Timeline ===
        self._slider = SeekSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 0)
        layout.addWidget(self._slider)

        # Time label
        time_row = QHBoxLayout()
        self._time_label = QLabel("00:00 / 00:00")
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        time_row.addWidget(self._time_label)
        self._delay_label = QLabel("")
        self._delay_label.setStyleSheet("color: #f6447f; font-size: 12px;")
        self._delay_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        time_row.addWidget(self._delay_label)
        layout.addLayout(time_row)

        # === Controls ===
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(8)

        self._btn_live = QPushButton("回到直播")
        self._btn_live.setObjectName("btn_live")
        self._btn_live.clicked.connect(self._on_go_live)
        self._btn_live.setVisible(False)
        ctrl_row.addWidget(self._btn_live)

        self._btn_play_pause = QPushButton("暂停")
        self._btn_play_pause.setCheckable(True)
        self._btn_play_pause.setChecked(True)
        self._btn_play_pause.clicked.connect(self._on_play_pause)
        ctrl_row.addWidget(self._btn_play_pause)

        ctrl_row.addSpacing(12)

        self._btn_mark_in = QPushButton("入点")
        self._btn_mark_in.setObjectName("btn_mark_in")
        self._btn_mark_in.clicked.connect(self._on_mark_in)
        ctrl_row.addWidget(self._btn_mark_in)

        self._btn_mark_out = QPushButton("出点")
        self._btn_mark_out.setObjectName("btn_mark_out")
        self._btn_mark_out.clicked.connect(self._on_mark_out)
        ctrl_row.addWidget(self._btn_mark_out)

        self._mark_label = QLabel("入点: --:--  出点: --:--")
        ctrl_row.addWidget(self._mark_label)

        ctrl_row.addStretch()

        self._btn_export = QPushButton("导出")
        self._btn_export.setObjectName("btn_export")
        self._btn_export.setEnabled(False)
        self._btn_export.clicked.connect(self._on_export)
        ctrl_row.addWidget(self._btn_export)

        layout.addLayout(ctrl_row)

        # === Status bar ===
        self._statusbar = self.statusBar()
        self._statusbar.showMessage("就绪")

        # === Core components ===
        self._recorder = FFmpegRecorder(self)
        self._cache = CacheManager(self)

        # === Signals ===
        self._recorder.state_changed.connect(self._on_recorder_state)

        self._cache.segments_changed.connect(self._on_segments_changed)

        self._player.position_changed.connect(self._on_position_changed)
        self._player.loaded.connect(self._on_player_loaded)

        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)

        log.info("ReLive ready")

        # Clean old cache on startup
        self._clean_cache()

        # Load room history
        self._load_history()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _on_connect(self):
        room_id = self._room_combo.currentText().strip()
        if not room_id:
            return

        if self._recorder.is_running():
            self._disconnect()
            return

        self._room_id = room_id
        quality_map = {0: "origin", 1: "hd", 2: "sd"}
        quality = quality_map.get(self._quality_combo.currentIndex(), "origin")

        self._btn_connect.setEnabled(False)
        self._status_label.setText("连接中...")
        self._status_label.setStyleSheet("color: #f6447f; font-size: 12px;")

        self._stream_worker = _StreamWorker(room_id, quality)
        self._stream_worker.finished.connect(self._on_stream_url)
        self._stream_worker.error.connect(self._on_stream_error)
        self._stream_worker.start()

    def _on_stream_url(self, url: str):
        if not url:
            log.warning("[CONNECT] empty URL, room may be offline")
            self._status_label.setText("未开播或出错")
            self._status_label.setStyleSheet("color: #f6447f; font-size: 12px;")
            self._btn_connect.setEnabled(True)
            return

        log.info("[CONNECT] stream URL obtained: %s", url[:120])
        self._connected = True
        self._stream_url = url

        # Start FFmpeg recorder
        log.info("[CONNECT] starting FFmpeg recorder...")
        self._recorder.start(url)

        # Start mpv live preview
        log.info("[CONNECT] starting mpv live preview...")
        self._player.reinitialize(url)
        self._is_live_mode = True
        log.info("[CONNECT] live mode active, recorder_running=%s", self._recorder.is_running())

        self._btn_connect.setText("断开")
        self._btn_connect.setEnabled(True)
        self._save_history(self._room_id)
        self._delay_label.setText("直播")
        self._delay_label.setStyleSheet("color: #10b981; font-size: 12px;")
        self._status_label.setText("直播中")
        self._status_label.setStyleSheet("color: #10b981; font-size: 12px;")

    def _on_stream_error(self, msg: str):
        log.error("stream error: %s", msg)
        self._status_label.setText(f"错误: {msg}")
        self._status_label.setStyleSheet("color: #f6447f; font-size: 12px;")
        self._btn_connect.setEnabled(True)

    def _disconnect(self):
        log.info("[DISCONNECT] disconnecting")
        self._connected = False
        self._is_live_mode = True
        self._recorder.stop()
        self._btn_connect.setText("连接")
        self._status_label.setText("未连接")
        self._btn_live.setVisible(False)
        self._status_label.setStyleSheet("color: #565f89; font-size: 12px;")

    # ------------------------------------------------------------------
    # Room history
    # ------------------------------------------------------------------

    def _clean_cache(self):
        """Delete old cache from previous sessions."""
        import shutil
        cache = config.CACHE_DIR
        if os.path.exists(cache):
            try:
                shutil.rmtree(cache)
                log.info("cleaned old cache: %s", cache)
            except OSError as e:
                log.warning("failed to clean cache: %s", e)
        os.makedirs(config.CACHE_DIR, exist_ok=True)

    def _load_history(self):
        """Load room history from disk."""
        try:
            if os.path.exists(config.HISTORY_PATH):
                with open(config.HISTORY_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                rooms = data.get("rooms", [])
                self._room_combo.clear()
                for room in rooms:
                    self._room_combo.addItem(str(room))
                if rooms:
                    self._room_combo.setCurrentIndex(0)
        except Exception as e:
            log.warning("failed to load history: %s", e)

    def _save_history(self, room_id: str):
        """Save room ID to history (move to top if exists)."""
        try:
            rooms = []
            for i in range(self._room_combo.count()):
                rooms.append(self._room_combo.itemText(i))
            # Move to top if already in list
            if room_id in rooms:
                rooms.remove(room_id)
            rooms.insert(0, room_id)
            # Keep max 20
            rooms = rooms[:20]

            with open(config.HISTORY_PATH, "w", encoding="utf-8") as f:
                json.dump({"rooms": rooms}, f, ensure_ascii=False, indent=2)

            # Update combo box
            self._room_combo.blockSignals(True)
            self._room_combo.clear()
            for room in rooms:
                self._room_combo.addItem(room)
            self._room_combo.setCurrentIndex(0)
            self._room_combo.blockSignals(False)
        except Exception as e:
            log.warning("failed to save history: %s", e)

    # ------------------------------------------------------------------
    # Recorder state
    # ------------------------------------------------------------------

    def _on_recorder_state(self, state: str):
        log.info("[RECORDER_STATE] state=%s is_live=%s connected=%s reconnect_count=%d",
                 state, self._is_live_mode, self._connected, self._reconnect_count)
        if state == "recording":
            self._status_label.setText("录制中")
            self._status_label.setStyleSheet("color: #10b981; font-size: 12px;")
            self._reconnect_count = 0
            log.info("[RECORDER_STATE] recording resumed, reconnect_count reset")
        elif state == "error":
            if self._is_live_mode:
                self._status_label.setText("录制缓存不可用，重试中...")
                self._status_label.setStyleSheet("color: #e0af68; font-size: 12px;")
                log.warning("[RECORDER_STATE] live mode error -> will reconnect")
            else:
                self._status_label.setText("流错误，重连中...")
                self._status_label.setStyleSheet("color: #f6447f; font-size: 12px;")
                log.warning("[RECORDER_STATE] DVR mode error -> will reconnect")
            self._try_reconnect()
        elif state == "stopped":
            self._status_label.setText("已停止")
            log.info("[RECORDER_STATE] recorder stopped")

    def _try_reconnect(self):
        if self._reconnect_count >= config.MAX_RECONNECT:
            log.error("[RECONNECT] max attempts (%d) reached, giving up", config.MAX_RECONNECT)
            self._status_label.setText("已断开（重试次数耗尽）")
            self._btn_connect.setText("连接")
            return

        self._reconnect_count += 1
        log.info("[RECONNECT] attempt %d/%d, waiting %ds before reconnect",
                 self._reconnect_count, config.MAX_RECONNECT, config.RECONNECT_WAIT)
        QTimer.singleShot(config.RECONNECT_WAIT * 1000, self._do_reconnect)

    def _do_reconnect(self):
        if not self._room_id:
            log.warning("[RECONNECT] no room_id, abort")
            return
        if not self._connected:
            log.warning("[RECONNECT] not connected, abort")
            return
        quality_map = {0: "origin", 1: "hd", 2: "sd"}
        quality = quality_map.get(self._quality_combo.currentIndex(), "origin")
        log.info("[RECONNECT] fetching new URL for room=%s quality=%s", self._room_id, quality)

        self._stream_worker = _StreamWorker(self._room_id, quality)
        self._stream_worker.finished.connect(self._on_reconnect_url)
        self._stream_worker.error.connect(lambda msg: (
            log.error("[RECONNECT] stream worker error: %s", msg),
            self._try_reconnect(),
        ))
        self._stream_worker.start()

    def _on_reconnect_url(self, url: str):
        if url:
            log.info("[RECONNECT] URL obtained: %s, restarting recorder. is_live=%s",
                     url[:80], self._is_live_mode)
            self._stream_url = url
            self._recorder.start(url)
        else:
            log.warning("[RECONNECT] empty URL, will retry")
            self._try_reconnect()

    # ------------------------------------------------------------------
    # Cache / segments
    # ------------------------------------------------------------------

    def _on_segments_changed(self):
        total = self._cache.total_duration()
        count = len(self._cache.segments)
        log.info("[CACHE] segments_changed: count=%d total=%.1fs range=[%.1f ~ %.1f]",
                 count, total, 0, total)
        if self._is_live_mode:
            self._slider.setRange(0, int(total * 100))
            self._slider.blockSignals(True)
            self._slider.setValue(self._slider.maximum())
            self._slider.blockSignals(False)
        # DVR mode: slider range frozen at snapshot duration, don't update
        self._statusbar.showMessage(f"缓存: {count} 段, {_fmt_time(total)}")
        self._btn_live.setVisible(self._connected and not self._is_live_mode)

    def _on_player_loaded(self):
        log.info("[LOADED] player loaded: is_live=%s pos=%.3f dur=%.3f",
                 self._is_live_mode, self._player.position(), self._player.duration())
        self._btn_play_pause.setChecked(True)
        self._btn_play_pause.setText("暂停")

    # ------------------------------------------------------------------
    # Playback position
    # ------------------------------------------------------------------

    def _on_position_changed(self, seconds: float):
        if self._seeking_from_slider:
            return

        total = self._cache.total_duration()

        if self._is_live_mode:
            self._slider.blockSignals(True)
            self._slider.setValue(self._slider.maximum())
            self._slider.blockSignals(False)
            self._time_label.setText(f"缓存: {_fmt_time(total)}")
        else:
            log.debug("[POS] DVR pos=%.3f slider=%d total=%.1f",
                      seconds, int(seconds * 100), total)
            self._slider.blockSignals(True)
            self._slider.setValue(int(seconds * 100))
            self._slider.blockSignals(False)
            self._time_label.setText(f"回看 {_fmt_time(seconds)} / {_fmt_time(self._dvr_frozen_duration)} · 直播 {_fmt_time(total)}")

    # ------------------------------------------------------------------
    # Slider (seek)
    # ------------------------------------------------------------------

    def _on_slider_pressed(self):
        log.info("slider pressed: value=%d", self._slider.value())
        self._seeking_from_slider = True
        self._user_interacting_slider = True

    def _on_slider_released(self):
        slider_val = self._slider.value()
        log.info("[SLIDER] === RELEASED === value=%d connected=%s live_mode=%s",
                 slider_val, self._connected, self._is_live_mode)
        self._seeking_from_slider = False
        self._user_interacting_slider = False

        if not self._connected:
            log.info("[SLIDER] not connected, skip")
            return

        total = self._cache.total_duration()
        if total <= 0:
            log.info("[SLIDER] total=0, skip")
            return

        target_sec = slider_val / 100.0  # convert from centiseconds
        log.info("[SLIDER] target=%.1fs / %.1fs (%.0f%%)",
                 target_sec, total, target_sec / total * 100 if total else 0)

        # Near the end — go live
        if target_sec >= total - config.SEGMENT_SEC * 2:
            log.info("[SLIDER] near end, going live")
            self._on_go_live()
            return

        # Find which segment to seek to
        result = self._cache.find_segment_at(target_sec)
        if result is None:
            log.warning("[SLIDER] find_segment_at(%.1f) returned None", target_sec)
            return

        seg_idx, offset_in_seg = result
        abs_offset = self._cache.get_absolute_time(seg_idx, 0)
        seek_to = abs_offset + offset_in_seg
        log.info("[SLIDER] DVR seek: seg_idx=%d offset=%.1fs abs_offset=%.1fs seek_to=%.1fs",
                 seg_idx, offset_in_seg, abs_offset, seek_to)

        if self._is_live_mode:
            # LIVE→DVR: first time entering replay, create snapshot + load
            log.info("[SLIDER] LIVE->DVR: snapshot, seek_to=%.1fs", seek_to)
            self._is_live_mode = False
            self._delay_label.setText("")
            self._btn_live.setVisible(True)
            snapshot_path, expected_dur = self._cache.write_snapshot()
            if not snapshot_path:
                log.warning("[SLIDER] write_snapshot failed, skip")
                return
            # Freeze slider range to snapshot duration
            self._dvr_frozen_duration = expected_dur
            self._slider.setRange(0, int(expected_dur * 100))
            self._player.reinitialize(snapshot_path, start_pos=seek_to,
                                      expected_duration=expected_dur)
        else:
            # DVR→DVR: snapshot already loaded, just seek
            log.info("[SLIDER] DVR->DVR: seek to %.1fs", seek_to)
            self._player.seek(seek_to)

    # ------------------------------------------------------------------
    # Controls
    # ------------------------------------------------------------------

    def _on_go_live(self):
        if self._is_live_mode:
            log.info("[GO_LIVE] already live, skip")
            return
        log.info("[GO_LIVE] === GO LIVE === connected=%s", self._connected)

        if not self._connected:
            log.info("[GO_LIVE] not connected, skip")
            return

        # Always refresh URL before going live (tokens expire)
        self._btn_live.setEnabled(False)
        self._btn_live.setText("刷新中...")
        quality_map = {0: "origin", 1: "hd", 2: "sd"}
        quality = quality_map.get(self._quality_combo.currentIndex(), "origin")
        self._go_live_worker = _StreamWorker(self._room_id, quality)
        self._go_live_worker.finished.connect(self._on_go_live_url)
        self._go_live_worker.error.connect(self._on_go_live_error)
        self._go_live_worker.start()

    def _on_go_live_url(self, url: str):
        log.info("[GO_LIVE] URL callback: url=%s", url[:80] if url else "EMPTY")
        if not url:
            log.error("[GO_LIVE] failed to refresh stream URL")
            self._status_label.setText("刷新地址失败")
            self._status_label.setStyleSheet("color: #f6447f; font-size: 12px;")
            self._btn_live.setText("回到直播")
            self._btn_live.setEnabled(True)
            return

        log.info("go live: URL refreshed, new mpv instance")
        self._stream_url = url
        self._is_live_mode = True
        self._user_interacting_slider = False
        self._btn_live.setVisible(False)
        self._btn_live.setEnabled(True)
        self._btn_live.setText("回到直播")
        self._delay_label.setText("直播")
        self._delay_label.setStyleSheet("color: #10b981; font-size: 12px;")
        total = self._cache.total_duration()
        self._slider.setRange(0, int(total * 100))
        self._slider.blockSignals(True)
        self._slider.setValue(self._slider.maximum())
        self._slider.blockSignals(False)
        self._player.reinitialize(url)

    def _on_go_live_error(self, msg: str):
        self._btn_live.setText("回到直播")
        self._btn_live.setEnabled(True)
        log.error("go live: URL refresh error: %s", msg)
        self._status_label.setText(f"错误: {msg}")
        self._status_label.setStyleSheet("color: #f6447f; font-size: 12px;")

    def _on_play_pause(self):
        if self._btn_play_pause.isChecked():
            self._btn_play_pause.setText("暂停")
            self._player.play()
        else:
            self._btn_play_pause.setText("播放")
            self._player.pause()

    def _on_mark_in(self):
        pos = self._player.position()
        self._mark_in_sec = max(0, pos)
        self._mark_out_sec = None
        self._update_mark_label()
        self._slider.set_mark_in_line(int(self._mark_in_sec * 100))
        self._slider.set_mark_out_line(None)
        self._update_export_button()

    def _on_mark_out(self):
        pos = self._player.position()
        self._mark_out_sec = max(0, pos)
        self._update_mark_label()
        self._slider.set_mark_out_line(int(self._mark_out_sec * 100))
        self._update_export_button()

    def _update_mark_label(self):
        inp = _fmt_time(self._mark_in_sec) if self._mark_in_sec is not None else "--:--"
        out = _fmt_time(self._mark_out_sec) if self._mark_out_sec is not None else "--:--"
        self._mark_label.setText(f"入点: {inp}  出点: {out}")

    def _update_export_button(self):
        can_export = (self._mark_in_sec is not None and self._mark_out_sec is not None
                      and self._mark_out_sec > self._mark_in_sec
                      and len(self._cache.segments) > 0)
        self._btn_export.setEnabled(can_export)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _on_export(self):
        if self._mark_in_sec is None or self._mark_out_sec is None:
            return
        if self._mark_out_sec <= self._mark_in_sec:
            QMessageBox.warning(self, "导出", "出点必须在入点之后")
            return

        # Find segments in range
        segs_in_range = []
        elapsed = 0.0
        for seg in self._cache.segments:
            seg_end = elapsed + seg.duration
            if seg_end > self._mark_in_sec and elapsed < self._mark_out_sec:
                segs_in_range.append(seg.path)
            elapsed = seg_end

        if not segs_in_range:
            QMessageBox.warning(self, "导出", "所选范围内无片段")
            return

        # Output path
        os.makedirs(config.EXPORT_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(config.EXPORT_DIR, f"relive_{ts}.mp4")

        self._btn_export.setEnabled(False)
        self._export_worker = _ExportWorker(segs_in_range, output_path)
        self._export_worker.finished.connect(self._on_export_done)
        self._export_worker.error.connect(self._on_export_error)
        self._export_worker.start()

    def _on_export_done(self, path: str):
        self._btn_export.setEnabled(True)
        self._statusbar.showMessage(f"已导出: {path}")
        log.info("export done: %s", path)

    def _on_export_error(self, msg: str):
        self._btn_export.setEnabled(True)
        self._statusbar.showMessage(f"导出失败: {msg}")
        log.error("export error: %s", msg)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if obj is self._video_widget and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Space:
                self._btn_play_pause.click()
                return True
            elif event.key() == Qt.Key.Key_L:
                self._on_go_live()
                return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        log.info("shutting down")
        self._recorder.stop()
        self._player.close()
        super().closeEvent(event)

    def __del__(self):
        """Fallback cleanup if closeEvent didn't run."""
        try:
            self._recorder.stop()
        except Exception:
            pass
