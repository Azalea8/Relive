"""ReLive configuration constants."""
import json
import os
import sys
from pathlib import Path

# Segment duration in seconds
SEGMENT_SEC = 4

# Maximum cache duration in hours (0 means no limit)
CACHE_HOURS = 2

# Cache check interval (ms) — for segment list updates
CACHE_CHECK_MS = 3000

# Orphan TS cleanup interval — delete files not in playlist every N hours
TS_CLEANUP_HOURS = 3

# mpv position poll interval (ms)
MPV_POLL_MS = 33

# Health check stall timeout (seconds)
STALL_TIMEOUT = 30

# Reconnect wait (seconds)
RECONNECT_WAIT = 5

# Max reconnect attempts
MAX_RECONNECT = 10

# Directories
if getattr(sys, "frozen", False):
    _BASE = os.path.dirname(sys.executable)
else:
    _BASE = str(Path(__file__).resolve().parent.parent)

CACHE_DIR = os.path.join(_BASE, "cache")
SEGMENT_DIR = os.path.join(CACHE_DIR, "videos")
M3U8_PATH = os.path.join(SEGMENT_DIR, "playlist.m3u8")
DANMAKU_DIR = os.path.join(CACHE_DIR, "danmaku")
EXPORT_DIR = os.path.join(_BASE, "exports")
BIN_DIR = os.path.join(_BASE, "bin")

# FFmpeg / ffprobe paths
_FFMPEG_BIN = os.path.join(BIN_DIR, "ffmpeg.exe")
FFMPEG_PATH = _FFMPEG_BIN if os.path.exists(_FFMPEG_BIN) else "ffmpeg"
_FFPROBE_BIN = os.path.join(BIN_DIR, "ffprobe.exe")
FFPROBE_PATH = _FFPROBE_BIN if os.path.exists(_FFPROBE_BIN) else "ffprobe"

# History file
HISTORY_PATH = os.path.join(_BASE, "history.json")
CONFIG_PATH = os.path.join(_BASE, "config.json")

# Default Douyu quality
DEFAULT_QUALITY = "origin"

# Danmaku rendering
DANMAKU_FONT_SIZE = 36
DANMAKU_DURATION = 14.0      # scroll duration (seconds)
DANMAKU_OPACITY = 0.8
DANMAKU_DM_RATE = 1.0        # fraction of screen height for danmaku
DANMAKU_DENSITY = 1.0
DANMAKU_OUTLINE_SIZE = 1.0
DANMAKU_OUTLINE_COLOR = "000000"

# Render progress update throttle (ms) — avoids excessive UI repaints
RENDER_PROGRESS_MS = 250

# Danmaku burn-in encoding defaults
RENDER_PRESET = "veryfast"   # x264 preset: ultrafast/fast/medium/slow — faster=bigger file
RENDER_CRF = 28              # x264 CRF quality (18=visually lossless, 23=default, 28=small)
RENDER_BITRATE = "15M"       # fallback bitrate for hardware encoders that lack CRF
RENDER_AUDIO_BITRATE = "192k"
RENDER_HW_QUALITY = "fast"  # fast / balanced / slow — maps to each HW encoder's preset

# ---------------------------------------------------------------------------
# Runtime overrides via config.json
# ---------------------------------------------------------------------------
def _load_user_config():
    if not os.path.exists(CONFIG_PATH):
        return
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    for key in ("CACHE_HOURS", "TS_CLEANUP_HOURS",
                "DANMAKU_FONT_SIZE", "DANMAKU_DURATION",
                "DANMAKU_OPACITY", "DANMAKU_DM_RATE",
                "RENDER_PRESET", "RENDER_HW_QUALITY",
                "RENDER_CRF", "RENDER_AUDIO_BITRATE"):
        if key in cfg:
            globals()[key] = cfg[key]

_load_user_config()
