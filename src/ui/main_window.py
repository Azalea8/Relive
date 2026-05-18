"""ReLive main window — live stream DVR with mpv playback."""
import json
import os
import time

from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
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

from src.playback.video_player import VideoPlayer
from src.recording.ffmpeg_recorder import FFmpegRecorder
from src.playback.cache_manager import CacheManager
from src.danmaku import DanmakuCollector, DanmakuManager, AssWriter, danmaku_to_ass, export_clip_ass
from src.logger import get as _log
from src import config
from src.ui.slider import SeekSlider
from src.ui.workers import StreamWorker, ExportWorker, RenderWorker

log = _log("ui")# Main Window
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

QPushButton#btn_danmaku {
    background-color: #2f3140;
    color: #a9b1d6;
    border: 1px solid #3b3d54;
    font-weight: 600;
}
QPushButton#btn_danmaku:checked {
    background-color: #e0af68;
    color: #1a1b26;
    border: none;
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
        self._stream_worker: StreamWorker | None = None
        self._export_worker: ExportWorker | None = None
        self._render_worker: RenderWorker | None = None
        self._exporting = False  # suppress status bar during export
        self._reconnect_count = 0

        # Danmaku state
        self._danmaku_enabled = True
        self._danmaku_ass_path = ""
        self._danmaku_live_start: float = 0.0
        self._dvr_ass_path = ""

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

        self._btn_danmaku = QPushButton("弹幕")
        self._btn_danmaku.setObjectName("btn_danmaku")
        self._btn_danmaku.setCheckable(True)
        self._btn_danmaku.setChecked(True)
        self._btn_danmaku.clicked.connect(self._on_danmaku_toggle)
        ctrl_row.addWidget(self._btn_danmaku)

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

        # === Danmaku components ===
        self._danmaku_collector = DanmakuCollector(self)
        self._danmaku_manager = DanmakuManager(self)
        self._ass_writer = AssWriter(width=1920, height=1080)

        # === Signals ===
        self._recorder.state_changed.connect(self._on_recorder_state)

        self._cache.segments_changed.connect(self._on_segments_changed)

        self._player.position_changed.connect(self._on_position_changed)
        self._player.loaded.connect(self._on_player_loaded)

        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)

        # Danmaku signals
        self._danmaku_collector.message_received.connect(self._danmaku_manager.on_raw_message)
        self._danmaku_manager.danmaku_added.connect(self._on_danmaku_live)

        # Danmaku sub-reload timer (QTimer on main thread — mpv API is not thread-safe)
        self._danmaku_reload_timer = QTimer(self)
        self._danmaku_reload_timer.timeout.connect(self._player.sub_reload)

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

        if self._connected:
            self._disconnect()
            return

        # Clear stale cache from previous session before reconnecting
        self._clean_cache()
        self._cache.segments.clear()
        self._cache._cached_total = 0.0
        self._cache._last_segment_count = -1
        self._cache._last_m3u8_size = 0
        self._slider.setRange(0, 0)

        self._room_id = room_id
        quality_map = {0: "origin", 1: "hd", 2: "sd"}
        quality = quality_map.get(self._quality_combo.currentIndex(), "origin")

        self._btn_connect.setEnabled(False)
        self._status_label.setText("连接中...")
        self._status_label.setStyleSheet("color: #f6447f; font-size: 12px;")

        self._stream_worker = StreamWorker(room_id, quality)
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

        # Prepare danmaku ASS before mpv init
        os.makedirs(config.DANMAKU_DIR, exist_ok=True)
        self._danmaku_manager.set_recording_start(self._recorder.start_time)
        self._danmaku_ass_path = os.path.join(config.DANMAKU_DIR, "live.ass")
        self._ass_writer = AssWriter(width=1920, height=1080)
        self._ass_writer.open_live(self._danmaku_ass_path)
        log.info("[CONNECT] ASS created: %s exists=%s",
                 self._danmaku_ass_path, os.path.exists(self._danmaku_ass_path))

        # Start mpv live preview with ASS subtitle
        log.info("[CONNECT] starting mpv live preview with sub_file=%s", self._danmaku_ass_path)
        self._player.reinitialize(url, sub_file=self._danmaku_ass_path)
        self._is_live_mode = True
        self._danmaku_live_start = time.time()  # reset danmaku timing for new live playback
        log.info("[CONNECT] live mode active, recorder_running=%s", self._recorder.is_running())

        # Start danmaku collection
        self._danmaku_collector.start(self._room_id)
        if self._danmaku_enabled:
            self._danmaku_reload_timer.start(1000)

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
        self._danmaku_reload_timer.stop()
        self._danmaku_collector.stop()
        self._danmaku_manager.clear()
        self._ass_writer.close()
        self._player.close()
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
        os.makedirs(config.SEGMENT_DIR, exist_ok=True)

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

        self._stream_worker = StreamWorker(self._room_id, quality)
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
        self._danmaku_manager.periodic_flush()
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
        if not self._exporting:
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
            self._time_label.setText("")
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

            # Generate DVR danmaku ASS
            self._danmaku_reload_timer.stop()
            self._dvr_ass_path = os.path.join(config.DANMAKU_DIR, "dvr.ass")
            ndjson = self._danmaku_manager.ndjson_path
            if ndjson and os.path.exists(ndjson):
                danmaku_to_ass(
                    ndjson, self._danmaku_manager.start_time * 1000,
                    self._dvr_ass_path, width=1920, height=1080,
                )
                self._player.reinitialize(snapshot_path, start_pos=seek_to,
                                          expected_duration=expected_dur)
                self._player.set_sub_file(self._dvr_ass_path)
            else:
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
        self._go_live_worker = StreamWorker(self._room_id, quality)
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
        self._danmaku_live_start = time.time()  # reset timing so danmaku align with new mpv playback
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

        # Prepare live danmaku ASS before reinitialize (same order as initial connect)
        self._danmaku_ass_path = os.path.join(config.DANMAKU_DIR, "live.ass")
        self._ass_writer = AssWriter(width=1920, height=1080)
        self._ass_writer.open_live(self._danmaku_ass_path)

        self._player.reinitialize(url, sub_file=self._danmaku_ass_path)

        if self._danmaku_enabled:
            self._danmaku_reload_timer.start(1000)

    def _on_go_live_error(self, msg: str):
        self._btn_live.setText("回到直播")
        self._btn_live.setEnabled(True)
        log.error("go live: URL refresh error: %s", msg)
        self._status_label.setText(f"错误: {msg}")
        self._status_label.setStyleSheet("color: #f6447f; font-size: 12px;")

    # ------------------------------------------------------------------
    # Danmaku
    # ------------------------------------------------------------------

    def _on_danmaku_live(self, msg: dict):
        msg_type = msg.get("msg_type", "unknown")
        if not self._is_live_mode or not self._danmaku_enabled:
            log.debug("[DANMAKU_LIVE] skip: is_live=%s enabled=%s type=%s",
                      self._is_live_mode, self._danmaku_enabled, msg_type)
            return
        if msg_type != "chat":
            log.debug("[DANMAKU_LIVE] skip non-chat: type=%s", msg_type)
            return
        # Use wall-clock time from most recent live playback start
        # (resets on each go-live so timestamps align with mpv's new stream position)
        elapsed = time.time() - self._danmaku_live_start
        content = msg.get("content", "")
        item = self._ass_writer.add(
            time_s=elapsed,
            text=content,
            color=msg.get("color", "ffffff"),
        )
        if item:
            self._ass_writer.append_to_file(item)
            if len(self._ass_writer._items) <= 5:
                log.info("[DANMAKU_LIVE] appended: elapsed=%.1f text=%s", elapsed, content[:30])
        else:
            log.debug("[DANMAKU_LIVE] collision-dropped: text=%s", content[:30])

    def _on_danmaku_toggle(self):
        self._danmaku_enabled = self._btn_danmaku.isChecked()
        if self._player._player:
            self._player._player.sub_visibility = self._danmaku_enabled
        log.info("[DANMAKU] toggle: enabled=%s", self._danmaku_enabled)

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

        # Ask for folder name
        default_name = time.strftime("%Y%m%d_%H%M%S")
        name, ok = QInputDialog.getText(
            self, "导出片段", "导出文件夹名称:",
            text=default_name,
        )
        if not ok or not name.strip():
            return
        name = name.strip()

        # Create export folder
        self._export_folder = os.path.join(config.EXPORT_DIR, name)
        os.makedirs(self._export_folder, exist_ok=True)

        ts = time.strftime("%Y%m%d_%H%M%S")
        self._export_base = os.path.join(self._export_folder, f"relive_{ts}")
        output_path = self._export_base + ".mp4"

        self._btn_export.setEnabled(False)
        self._exporting = True
        self._status(f"导出中 → {name}/")

        self._export_worker = ExportWorker(segs_in_range, output_path)
        self._export_worker.finished.connect(self._on_export_done)
        self._export_worker.error.connect(self._on_export_error)
        self._export_worker.start()

    def _status(self, msg: str):
        folder = getattr(self, '_export_folder', '')
        if folder:
            name = os.path.basename(folder)
            self._statusbar.showMessage(f"{msg} → {name}/")
        else:
            self._statusbar.showMessage(msg)

    def _finish_export(self):
        """Clear export state, marks, and restore status bar."""
        self._exporting = False
        self._mark_in_sec = None
        self._mark_out_sec = None
        self._update_mark_label()
        self._slider.set_mark_in_line(None)
        self._slider.set_mark_out_line(None)
        self._update_export_button()
        total = self._cache.total_duration()
        count = len(self._cache.segments)
        self._statusbar.showMessage(f"缓存: {count} 段, {_fmt_time(total)}")

    def _on_export_done(self, path: str):
        log.info("video export done: %s", path)

        ndjson = self._danmaku_manager.ndjson_path
        if ndjson and os.path.exists(ndjson):
            ass_path = self._export_base + ".ass"
            self._status("生成弹幕字幕")
            count = export_clip_ass(
                ndjson, self._danmaku_manager.start_time * 1000,
                self._mark_in_sec, self._mark_out_sec, ass_path,
            )
            log.info("clip ASS generated: %d danmaku -> %s", count, ass_path)

            dm_path = self._export_base + "_dm.mp4"
            self._status("渲染弹幕")
            self._render_worker = RenderWorker(path, ass_path, dm_path)
            self._render_worker.finished.connect(self._on_render_done)
            self._render_worker.error.connect(self._on_render_error)
            self._render_worker.start()
        else:
            self._btn_export.setEnabled(True)
            self._status("已导出")
            self._finish_export()

    def _on_export_error(self, msg: str):
        self._btn_export.setEnabled(True)
        self._status(f"导出失败: {msg}")
        log.error("export error: %s", msg)
        self._finish_export()

    def _on_render_done(self, path: str):
        self._btn_export.setEnabled(True)
        self._status("已导出（含弹幕）")
        log.info("render done: %s", path)
        self._finish_export()

    def _on_render_error(self, msg: str):
        self._btn_export.setEnabled(True)
        self._status("已导出（弹幕渲染失败）")
        log.error("render error: %s", msg)
        self._finish_export()

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
        self._danmaku_collector.stop()
        self._danmaku_manager.clear()
        self._danmaku_reload_timer.stop()
        self._recorder.stop()
        self._player.close()
        super().closeEvent(event)

    def __del__(self):
        """Fallback cleanup if closeEvent didn't run."""
        try:
            self._recorder.stop()
        except Exception:
            pass
