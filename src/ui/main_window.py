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
    QProgressBar,
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
from src.ui.slider import SeekSlider, DensityOverlay
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
        self._base_offset_sec: float = 0.0
        self._seeking_from_slider = False
        self._dvr_frozen_duration = 0.0
        self._stream_worker: StreamWorker | None = None
        self._export_worker: ExportWorker | None = None
        self._render_worker: RenderWorker | None = None
        self._render_with_danmaku = True  # set by export dialog
        self._exporting = False  # suppress status bar during export
        self._reconnect_count = 0
        self._fullscreen = False
        self._start_time = time.monotonic()

        # Danmaku state
        self._danmaku_enabled = True
        self._danmaku_ass_path = ""
        self._danmaku_live_start: float = 0.0
        self._dvr_ass_path = ""

        # --- central widget ---
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # === Top chrome container ===
        self._top_chrome = QWidget()
        tc_layout = QVBoxLayout(self._top_chrome)
        tc_layout.setContentsMargins(6, 6, 6, 2)
        tc_layout.setSpacing(4)

        # === Top bar: connection controls ===
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        self._platform_combo = QComboBox()
        self._platform_combo.addItems(["斗鱼"])
        self._platform_combo.setMinimumWidth(64)
        self._platform_combo.setStyleSheet("font-size: 13px;")
        top_row.addWidget(self._platform_combo)

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

        tc_layout.addLayout(top_row)
        layout.addWidget(self._top_chrome)

        # === Video area ===
        self._video_widget = QWidget()
        self._video_widget.setStyleSheet("background-color: black")
        self._video_widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._video_widget.installEventFilter(self)
        layout.addWidget(self._video_widget, stretch=1)

        # Player
        self._player = VideoPlayer(self._video_widget)

        # === Bottom chrome container ===
        self._bottom_chrome = QWidget()
        bc_layout = QVBoxLayout(self._bottom_chrome)
        bc_layout.setContentsMargins(6, 2, 6, 6)
        bc_layout.setSpacing(4)

        # Density bar (between video and slider)
        self._density_overlay = DensityOverlay()
        bc_layout.addWidget(self._density_overlay)

        # === Timeline ===
        self._slider = SeekSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 0)
        bc_layout.addWidget(self._slider)

        # Time label
        time_row = QHBoxLayout()
        self._time_label = QLabel("")
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        time_row.addWidget(self._time_label)
        self._delay_label = QLabel("")
        self._delay_label.setStyleSheet("color: #f6447f; font-size: 12px;")
        self._delay_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        time_row.addStretch()
        time_row.addWidget(self._delay_label)
        bc_layout.addLayout(time_row)

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

        self._btn_fullscreen = QPushButton("全屏")
        self._btn_fullscreen.clicked.connect(self._on_fullscreen)
        ctrl_row.addWidget(self._btn_fullscreen)

        self._btn_settings = QPushButton("设置")
        self._btn_settings.clicked.connect(self._on_settings)
        ctrl_row.addWidget(self._btn_settings)

        self._btn_export = QPushButton("导出")
        self._btn_export.setObjectName("btn_export")
        self._btn_export.setEnabled(False)
        self._btn_export.clicked.connect(self._on_export)
        ctrl_row.addWidget(self._btn_export)

        self._render_progress = QProgressBar()
        self._render_progress.setMaximum(100)
        self._render_progress.setMaximumWidth(150)
        self._render_progress.setMaximumHeight(18)
        self._render_progress.setVisible(False)
        ctrl_row.addWidget(self._render_progress)

        self._btn_cancel_render = QPushButton("取消")
        self._btn_cancel_render.setObjectName("btn_cancel_render")
        self._btn_cancel_render.setVisible(False)
        self._btn_cancel_render.clicked.connect(self._on_cancel_render)
        ctrl_row.addWidget(self._btn_cancel_render)

        bc_layout.addLayout(ctrl_row)
        layout.addWidget(self._bottom_chrome)

        # === Status bar ===
        self._statusbar = self.statusBar()
        self._statusbar.showMessage("就绪")

        self._runtime_label = QLabel("00:00")
        self._runtime_label.setStyleSheet("font-size: 11px; padding-right: 4px;")
        self._statusbar.addPermanentWidget(self._runtime_label)

        self._runtime_timer = QTimer(self)
        self._runtime_timer.timeout.connect(self._update_runtime)
        self._runtime_timer.start(1000)

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
        self._cache._cached_total = 0.0
        self._cache._last_segment_count = -1
        self._slider.setRange(0, 0)

        self._room_id = room_id
        quality_map = {0: "origin", 1: "hd", 2: "sd"}
        quality = quality_map.get(self._quality_combo.currentIndex(), "origin")

        self._btn_connect.setEnabled(False)
        self._status_label.setText("连接中...")
        self._status_label.setStyleSheet("color: #f6447f; font-size: 12px;")

        self._stream_worker = StreamWorker(room_id, quality, self._platform())
        self._stream_worker.finished.connect(self._on_stream_url)
        self._stream_worker.error.connect(self._on_stream_error)
        self._stream_worker.start()

    def _platform(self) -> str:
        return "huya" if self._platform_combo.currentIndex() == 1 else "douyu"

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
        self._danmaku_collector.start(self._room_id, self._platform())
        if self._danmaku_enabled:
            self._danmaku_reload_timer.start(1000)

        self._btn_connect.setText("断开")
        self._btn_connect.setEnabled(True)
        self._save_history(self._room_id)
        self._time_label.setText("")
        self._delay_label.setText("直播")
        self._delay_label.setStyleSheet("color: #10b981; font-size: 12px;")

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
        self._time_label.setText("")
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

        self._stream_worker = StreamWorker(self._room_id, quality, self._platform())
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
            # Clear stale video segments so the new connection's cache
            # starts from a consistent time base.
            import shutil
            if os.path.exists(config.SEGMENT_DIR):
                try:
                    shutil.rmtree(config.SEGMENT_DIR)
                except OSError:
                    pass
            os.makedirs(config.SEGMENT_DIR, exist_ok=True)
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
        count = self._cache.segment_count
        log.info("[CACHE] segments_changed: count=%d total=%.1fs range=[%.1f ~ %.1f]",
                 count, total, 0, total)
        if self._is_live_mode:
            self._slider.setRange(0, int(total * 100))
            self._slider.blockSignals(True)
            self._slider.setValue(self._slider.maximum())
            self._slider.blockSignals(False)
        if not self._exporting:
            self._statusbar.showMessage(f"缓存: {count} 段 | 总时长: {_fmt_time(total)}")
        self._btn_live.setVisible(self._connected and not self._is_live_mode)
        self._btn_fullscreen.setEnabled(self._is_live_mode)

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
            # Show real clock time from segment timestamps
            t_dvr = self._wall_clock_at(seconds)
            t_end = self._wall_clock_at(self._dvr_frozen_duration)
            self._time_label.setText(f"回看 {t_dvr} / {t_end}")

    def _update_runtime(self):
        """Update the status bar runtime label."""
        sec = int(time.monotonic() - self._start_time)
        h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
        if h > 0:
            self._runtime_label.setText(f"运行 {h}:{m:02d}:{s:02d}")
        else:
            self._runtime_label.setText(f"运行 {m:02d}:{s:02d}")

    def _wall_clock_at(self, offset: float) -> str:
        """Convert a recording-relative offset to wall-clock HH:MM:SS."""
        import re
        from datetime import datetime
        from src import config
        # Prefer snapshot's first TS so wall clock stays pinned to DVR timeline
        first = self._cache.snapshot_first_ts
        if not first:
            return "--:--:--"
        m = re.match(r'^(\d{8}_\d{6})_(\d{6})\.ts$', os.path.basename(first))
        if not m:
            return "--:--:--"
        try:
            prefix = m.group(1)  # e.g. "20260522_125049"
            seq = int(m.group(2))  # e.g. 354
            seg_abs = datetime.strptime(prefix, "%Y%m%d_%H%M%S").timestamp()
            seg_abs += (seq - 1) * config.SEGMENT_SEC
            t = seg_abs + offset
            dt = datetime.fromtimestamp(t)
            return dt.strftime("%H:%M:%S")
        except (ValueError, OSError):
            return "--:--:--"

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

        # mpv's HLS demuxer handles segment-level seek — just pass target time
        seek_to = target_sec
        log.info("[SLIDER] DVR seek: seek_to=%.1fs", seek_to)

        if self._is_live_mode:
            # LIVE→DVR: first time entering replay, create snapshot + load
            log.info("[SLIDER] LIVE->DVR: snapshot, seek_to=%.1fs", seek_to)
            self._is_live_mode = False
            self._base_offset_sec = self._cache.get_first_segment_base_sec(
                self._danmaku_manager.start_time)
            log.info(f"[SLIDER] _base_offset_sec: {self._base_offset_sec}")
            self._delay_label.setText("")
            self._btn_live.setVisible(True)
            snapshot_path, expected_dur = self._cache.write_snapshot()
            if not snapshot_path:
                log.warning("[SLIDER] write_snapshot failed, skip")
                return
            # Freeze slider range to snapshot duration
            self._dvr_frozen_duration = expected_dur
            self._slider.setRange(0, int(expected_dur * 100))

            # Danmaku density curve
            ndjson = self._danmaku_manager.ndjson_path
            if ndjson and os.path.exists(ndjson):
                buckets = self._cache.get_density_buckets(
                    ndjson, self._danmaku_manager.start_time * 1000,
                    expected_dur,
                    base_offset_sec=self._base_offset_sec)
                self._density_overlay.set_density(buckets)

            # Generate DVR danmaku ASS
            self._danmaku_reload_timer.stop()
            self._dvr_ass_path = os.path.join(config.DANMAKU_DIR, "dvr.ass")
            ndjson = self._danmaku_manager.ndjson_path
            if ndjson and os.path.exists(ndjson):
                danmaku_to_ass(
                    ndjson, self._danmaku_manager.start_time * 1000,
                    self._dvr_ass_path, width=1920, height=1080,
                    base_offset_sec=self._base_offset_sec,
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
        self._go_live_worker = StreamWorker(self._room_id, quality, self._platform())
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
        self._density_overlay.clear_density()
        self._user_interacting_slider = False
        self._btn_live.setVisible(False)
        self._btn_live.setEnabled(True)
        self._btn_live.setText("回到直播")
        self._time_label.setText("")
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
            if self._ass_writer._live_line_count <= 5:
                log.info("[DANMAKU_LIVE] appended: elapsed=%.1f text=%s", elapsed, content[:30])
        else:
            log.debug("[DANMAKU_LIVE] collision-dropped: text=%s", content[:30])

    def _on_settings(self):
        from PyQt6.QtWidgets import (QDialog, QFormLayout, QDoubleSpinBox,
                                      QSpinBox, QDialogButtonBox, QLabel)
        dlg = QDialog(self)
        dlg.setWindowTitle("设置")
        dlg.resize(360, 320)

        layout = QVBoxLayout(dlg)
        form = QFormLayout()

        cache_h = QDoubleSpinBox()
        cache_h.setRange(0.05, 24); cache_h.setValue(config.CACHE_HOURS); cache_h.setSuffix(" 小时")
        form.addRow("可回看时长（相对于直播）", cache_h)

        cleanup_h = QDoubleSpinBox()
        cleanup_h.setRange(0.05, 24); cleanup_h.setValue(config.TS_CLEANUP_HOURS); cleanup_h.setSuffix(" 小时")
        form.addRow("定时清理缓存视频（不易过小）", cleanup_h)

        font_sz = QSpinBox()
        font_sz.setRange(12, 72); font_sz.setValue(config.DANMAKU_FONT_SIZE)
        form.addRow("弹幕字号", font_sz)

        dur = QDoubleSpinBox()
        dur.setRange(5, 30); dur.setValue(config.DANMAKU_DURATION); dur.setSuffix(" 秒")
        form.addRow("弹幕飘动时间(控制移动速度,数值越小导出越快)", dur)

        opacity = QDoubleSpinBox()
        opacity.setRange(0, 1); opacity.setSingleStep(0.05); opacity.setValue(config.DANMAKU_OPACITY)
        form.addRow("弹幕透明度", opacity)

        dmrate = QDoubleSpinBox()
        dmrate.setRange(0.1, 1.0); dmrate.setSingleStep(0.1); dmrate.setValue(config.DANMAKU_DM_RATE)
        form.addRow("弹幕占屏比例", dmrate)

        layout.addLayout(form)
        layout.addSpacing(8)

        note1 = QLabel("导出默认使用 GPU 加速，CPU 兜底")
        note1.setStyleSheet("color: #e0af68; font-size: 12px;")
        layout.addWidget(note1)

        layout.addSpacing(8)

        note2 = QLabel("修改后需重启应用生效")
        note2.setStyleSheet("color: #e0fa86; font-size: 15px;")
        layout.addWidget(note2)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        data = {
            "CACHE_HOURS": cache_h.value(),
            "TS_CLEANUP_HOURS": cleanup_h.value(),
            "DANMAKU_FONT_SIZE": font_sz.value(),
            "DANMAKU_DURATION": dur.value(),
            "DANMAKU_OPACITY": opacity.value(),
            "DANMAKU_DM_RATE": dmrate.value(),
        }
        with open(config.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

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
        # base = (self._base_offset_sec or 0) + self._danmaku_manager.start_time
        inp = self._wall_clock_at((self._mark_in_sec)) if self._mark_in_sec is not None else "--:--"
        out = self._wall_clock_at((self._mark_out_sec)) if self._mark_out_sec is not None else "--:--"
        self._mark_label.setText(f"入点: {inp}  出点: {out}")

    def _update_export_button(self):
        can_export = (self._mark_in_sec is not None and self._mark_out_sec is not None
                      and self._mark_out_sec > self._mark_in_sec
                      and self._cache.segment_count > 0)
        self._btn_export.setEnabled(can_export)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    @staticmethod
    def _log_write(log_path: str | None, msg: str) -> None:
        """Append a timestamped line to a log file."""
        if not log_path:
            return
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{ts}  {msg}\n")
        except OSError:
            pass

    def _on_export(self):
        if self._mark_in_sec is None or self._mark_out_sec is None:
            return
        if self._mark_out_sec <= self._mark_in_sec:
            QMessageBox.warning(self, "导出", "出点必须在入点之后")
            return

        segs_in_range = self._cache.get_paths_in_range(
            self._mark_in_sec, self._mark_out_sec)

        if not segs_in_range:
            QMessageBox.warning(self, "导出", "所选范围内无片段")
            return

        has_danmaku = (self._danmaku_manager.ndjson_path is not None
                       and os.path.exists(self._danmaku_manager.ndjson_path))

        # --- Export config dialog ---
        from PyQt6.QtWidgets import (QDialog, QFormLayout, QCheckBox,
                                      QComboBox, QDialogButtonBox, QLabel,
                                      QLineEdit, QGroupBox)

        dlg = QDialog(self)
        dlg.setWindowTitle("导出片段")
        dlg.resize(400, 320)

        layout = QVBoxLayout(dlg)

        # Folder name
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("文件夹名称:"))
        name_edit = QLineEdit(time.strftime("%Y%m%d_%H%M%S"))
        name_row.addWidget(name_edit)
        layout.addLayout(name_row)

        layout.addSpacing(8)

        # Danmaku checkbox
        dm_check = QCheckBox("烧录弹幕到视频")
        dm_check.setChecked(True)
        dm_check.setVisible(has_danmaku)
        layout.addWidget(dm_check)

        # --- Render settings group ---
        render_group = QGroupBox("渲染设置")
        render_form = QFormLayout(render_group)

        sw_presets = ["ultrafast", "superfast", "veryfast", "faster",
                      "fast", "medium", "slow"]
        sw_combo = QComboBox()
        for p in sw_presets:
            sw_combo.addItem(p, p)
        idx = sw_combo.findData(config.RENDER_PRESET)
        if idx >= 0:
            sw_combo.setCurrentIndex(idx)
        sw_row = QHBoxLayout()
        sw_row.addWidget(sw_combo)
        sw_hint = QLabel("(libx264)")
        sw_hint.setStyleSheet("color: #888; font-size: 11px;")
        sw_row.addWidget(sw_hint)
        sw_row.addStretch()
        render_form.addRow("软编码速度（兜底）", sw_row)

        hw_labels = {"fast": "快", "balanced": "均衡", "slow": "慢"}
        hw_combo = QComboBox()
        for key, label in hw_labels.items():
            hw_combo.addItem(label, key)
        idx = hw_combo.findData(config.RENDER_HW_QUALITY)
        if idx >= 0:
            hw_combo.setCurrentIndex(idx)
        hw_row = QHBoxLayout()
        hw_row.addWidget(hw_combo)
        hw_hint = QLabel("(NVENC/QSV/AMF)")
        hw_hint.setStyleSheet("color: #888; font-size: 11px;")
        hw_row.addWidget(hw_hint)
        hw_row.addStretch()
        render_form.addRow("硬编码速度（默认）", hw_row)

        crf_opts = [(18, "高 (CRF 18)"), (23, "较高 (CRF 23)"), (28, "标准 (CRF 28)")]
        crf_combo = QComboBox()
        for val, label in crf_opts:
            crf_combo.addItem(label, val)
        idx = crf_combo.findData(config.RENDER_CRF)
        if idx >= 0:
            crf_combo.setCurrentIndex(idx)
        render_form.addRow("画质", crf_combo)

        layout.addWidget(render_group)

        # Toggle render group visibility with checkbox
        def _toggle_render(checked):
            render_group.setVisible(checked)

        dm_check.toggled.connect(_toggle_render)

        # Buttons
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        name = name_edit.text().strip()
        if not name:
            return

        render_with_dm = dm_check.isChecked() and has_danmaku
        self._render_with_danmaku = render_with_dm

        # Persist render settings to config.json + update globals for this session
        new_preset = sw_combo.currentData()
        new_hw_quality = hw_combo.currentData()
        new_crf = crf_combo.currentData()
        config.RENDER_PRESET = new_preset
        config.RENDER_HW_QUALITY = new_hw_quality
        config.RENDER_CRF = new_crf
        self._save_render_config(new_preset, new_hw_quality, new_crf)

        # Create export folder + log
        self._export_folder = os.path.join(config.EXPORT_DIR, name)
        os.makedirs(self._export_folder, exist_ok=True)

        self._export_log = os.path.join(self._export_folder, "export.log")
        self._export_start_time = time.monotonic()
        self._log_write(self._export_log,
            f"导出开始  入点: {self._mark_in_sec:.1f}s  出点: {self._mark_out_sec:.1f}s  "
            f"片段: {len(segs_in_range)}  目录: {self._export_folder}")
        self._log_write(self._export_log,
            f"渲染设置  preset={config.RENDER_PRESET}  "
            f"hw_quality={config.RENDER_HW_QUALITY}  crf={config.RENDER_CRF}"
            f"  {'烧录弹幕' if render_with_dm else '跳过弹幕'}")

        ts = time.strftime("%Y%m%d_%H%M%S")
        self._export_base = os.path.join(self._export_folder, f"relive_{ts}")
        output_path = self._export_base + ".mp4"

        self._btn_export.setEnabled(False)
        self._exporting = True
        self._status(f"导出中 → {name}/")

        self._export_worker = ExportWorker(segs_in_range, output_path,
                                           log_path=self._export_log)
        self._export_worker.finished.connect(self._on_export_done)
        self._export_worker.error.connect(self._on_export_error)
        self._export_worker.start()

    def _save_render_config(self, preset: str, hw_quality: str, crf: int):
        """Persist render settings to config.json, merging with existing keys."""
        try:
            if os.path.exists(config.CONFIG_PATH):
                with open(config.CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            else:
                cfg = {}
        except (OSError, json.JSONDecodeError):
            cfg = {}
        cfg["RENDER_PRESET"] = preset
        cfg["RENDER_HW_QUALITY"] = hw_quality
        cfg["RENDER_CRF"] = crf
        with open(config.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

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
        count = self._cache.segment_count
        self._statusbar.showMessage(f"缓存: {count} 段 | 总时长: {_fmt_time(total)}")

    def _on_export_done(self, path: str):
        log.info("video export done: %s", path)

        ndjson = self._danmaku_manager.ndjson_path
        render_dm = self._render_with_danmaku and ndjson and os.path.exists(ndjson)

        if render_dm:
            ass_path = self._export_base + ".ass"
            self._status("生成弹幕字幕")
            self._log_write(self._export_log, "生成弹幕 ASS 字幕")
            count = export_clip_ass(
                ndjson, self._danmaku_manager.start_time * 1000,
                self._mark_in_sec, self._mark_out_sec, ass_path,
                base_offset_sec=(self._base_offset_sec or 0),
            )
            log.info("clip ASS generated: %d danmaku -> %s", count, ass_path)
            self._log_write(self._export_log, f"ASS 弹幕: {count} 条")

            dm_path = self._export_base + "_dm.mp4"
            self._status("渲染弹幕")
            self._render_worker = RenderWorker(path, ass_path, dm_path,
                                                log_path=self._export_log)
            self._render_worker.finished.connect(self._on_render_done)
            self._render_worker.error.connect(self._on_render_error)
            self._render_worker.progress.connect(self._on_render_progress)
            self._render_worker.start()

            self._render_progress.setValue(0)
            self._render_progress.setVisible(True)
            self._btn_cancel_render.setEnabled(True)
            self._btn_cancel_render.setVisible(True)
        else:
            elapsed = time.monotonic() - self._export_start_time
            self._log_write(self._export_log,
                f"导出完成  输出: {path}  总耗时: {elapsed:.1f}s")
            self._btn_export.setEnabled(True)
            self._status("已导出")
            self._finish_export()

    def _on_export_error(self, msg: str):
        self._log_write(getattr(self, '_export_log', None),
            f"导出失败: {msg}")
        self._btn_export.setEnabled(True)
        self._status(f"导出失败: {msg}")
        log.error("export error: %s", msg)
        self._finish_export()

    def _on_render_done(self, path: str):
        elapsed = time.monotonic() - self._export_start_time
        self._log_write(self._export_log,
            f"导出完成  输出: {path}  总耗时: {elapsed:.1f}s")
        self._btn_export.setEnabled(True)
        self._status("已导出（含弹幕）")
        log.info("render done: %s", path)
        self._render_progress.setVisible(False)
        self._btn_cancel_render.setVisible(False)
        self._finish_export()

    def _on_render_error(self, msg: str):
        self._log_write(self._export_log,
            f"渲染失败: {msg}")
        self._btn_export.setEnabled(True)
        if "取消" in msg:
            self._status("已取消导出")
        else:
            self._status("已导出（弹幕渲染失败）")
        log.error("render error: %s", msg)
        self._render_progress.setVisible(False)
        self._btn_cancel_render.setVisible(False)
        self._finish_export()

    def _on_render_progress(self, pct: int, status: str):
        self._render_progress.setValue(pct)
        self._status(f"渲染弹幕 ({pct}% - {status})")

    def _on_cancel_render(self):
        if self._render_worker is not None and self._render_worker.isRunning():
            self._render_worker.cancel()
            self._btn_cancel_render.setEnabled(False)
            self._status("取消渲染中...")

    # ------------------------------------------------------------------
    # Fullscreen
    # ------------------------------------------------------------------

    def _on_fullscreen(self):
        if not self._is_live_mode:
            return
        self._fullscreen = True
        self._top_chrome.hide()
        self._bottom_chrome.hide()
        self._statusbar.hide()
        self.showFullScreen()

    def _exit_fullscreen(self):
        self._fullscreen = False
        self._top_chrome.show()
        self._bottom_chrome.show()
        self._statusbar.show()
        self.showNormal()

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if obj is self._video_widget and event.type() == QEvent.Type.KeyPress:
                if event.key() == Qt.Key.Key_Escape and self._fullscreen:
                    self._exit_fullscreen()
                    return True
                if event.key() == Qt.Key.Key_Space:
                    self._btn_play_pause.click()
                    return True
                elif event.key() == Qt.Key.Key_L:
                    self._on_go_live()
                    return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        log.info("shutting down")
        if self._fullscreen:
            self._exit_fullscreen()
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
