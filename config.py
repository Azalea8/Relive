"""ReLive configuration constants."""
import os

# Segment duration in seconds
SEGMENT_SEC = 4

# Maximum cache duration in hours
CACHE_HOURS = 2

# Cache check interval (ms) — for segment list updates
CACHE_CHECK_MS = 3000

# mpv position poll interval (ms)
MPV_POLL_MS = 33

# Health check stall timeout (seconds)
STALL_TIMEOUT = 30

# Reconnect wait (seconds)
RECONNECT_WAIT = 5

# Max reconnect attempts
MAX_RECONNECT = 10

# Directories
if getattr(__import__("sys"), "frozen", False):
    _BASE = os.path.dirname(__import__("sys").executable)
else:
    _BASE = os.path.dirname(os.path.abspath(__file__))

CACHE_DIR = os.path.join(_BASE, "cache")
SEGMENT_DIR = os.path.join(CACHE_DIR, "videos")
M3U8_PATH = os.path.join(SEGMENT_DIR, "playlist.m3u8")
DANMAKU_DIR = os.path.join(CACHE_DIR, "danmaku")
EXPORT_DIR = os.path.join(_BASE, "exports")
BIN_DIR = os.path.join(_BASE, "bin")

# FFmpeg path
FFMPEG_PATH = os.path.join(BIN_DIR, "ffmpeg.exe") if os.path.exists(os.path.join(BIN_DIR, "ffmpeg.exe")) else "ffmpeg"

# History file
HISTORY_PATH = os.path.join(_BASE, "history.json")

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
