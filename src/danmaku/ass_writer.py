"""ASS 字幕生成器 — 弹幕飘动+颜色+碰撞检测"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src import config


def _sec2hms(seconds: float) -> tuple:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return h, m, s


def _rgb2bgr(color: str) -> str:
    if len(color) != 6:
        return "FFFFFF"
    return color[4:6] + color[2:4] + color[0:2]


def _format_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    h, m, s = _sec2hms(seconds)
    return f"{h}:{m:02d}:{s:05.2f}"


@dataclass
class DanmakuItem:
    time_s: float
    text: str
    color: str = "ffffff"
    track_id: int = 0
    text_width: int = 0


@dataclass
class _Track:
    tid: int
    last_item: Optional[DanmakuItem] = None
    last_text_width: int = 0


class AssWriter:
    """ASS 字幕生成器"""

    def __init__(self, width: int = 1920, height: int = 1080,
                 font: str = "Microsoft YaHei", fontsize: int = 0,
                 duration: float = 0.0, opacity: float = 0.0,
                 dmrate: float = 0.0, margin_h: int = 6,
                 outline_size: float = 0.0, outline_color: str = "",
                 density: float = 0.0):
        self.width = width
        self.height = height
        self.font = font
        self.fontsize = fontsize or config.DANMAKU_FONT_SIZE
        self.duration = duration or config.DANMAKU_DURATION
        self.opacity = opacity or config.DANMAKU_OPACITY
        self.dmrate = dmrate or config.DANMAKU_DM_RATE
        self.margin_h = margin_h
        self.outline_size = outline_size or config.DANMAKU_OUTLINE_SIZE
        self.outline_color = outline_color or config.DANMAKU_OUTLINE_COLOR
        self.density = max(0.1, density or config.DANMAKU_DENSITY)
        self.dst = 20

        self._ntracks = int(((self.height - self.dst) * self.dmrate) / (self.fontsize + self.margin_h))
        self._tracks = [_Track(i) for i in range(self._ntracks)]
        self._items: list[DanmakuItem] = []
        self._live_path: str = ""
        self._live_fh = None

        self._opacity_hex = f"{int((1 - self.opacity) * 255):02X}"
        self._outline_bgr = _rgb2bgr(self.outline_color)
        self._half_font = self.fontsize * 0.5

    def _get_text_width(self, text: str) -> int:
        half = self._half_font
        full = self.fontsize
        length = 0
        for ch in text:
            if ord(ch) < 128:
                length += half
            else:
                length += full
        return int(length)

    def _find_track(self, time_s: float, text_width: int) -> Optional[int]:
        min_gap = text_width * 0.5 / self.density
        duration = self.duration
        w = self.width

        fallback_tid = -1

        for track in self._tracks:
            if track.last_item is None:
                if fallback_tid < 0:
                    fallback_tid = track.tid
                continue

            elapsed = time_s - track.last_item.time_s
            if elapsed >= duration:
                return track.tid
            speed = (track.last_text_width + w) / duration
            dist = elapsed * speed - track.last_text_width

            if dist >= min_gap:
                return track.tid

        return fallback_tid if fallback_tid >= 0 else None

    def add(self, time_s: float, text: str, color: str = "ffffff") -> Optional[DanmakuItem]:
        if "\n" in text or "\r" in text:
            text = text.replace("\n", " ").replace("\r", " ")
        text = text.strip()
        if not text:
            return None

        text_width = self._get_text_width(text)
        tid = self._find_track(time_s, text_width)
        if tid is None:
            return None

        item = DanmakuItem(time_s=time_s, text=text, color=color, track_id=tid, text_width=text_width)

        track = self._tracks[tid]
        track.last_item = item
        track.last_text_width = text_width
        self._items.append(item)
        return item

    def _build_header(self) -> str:
        oh = self._opacity_hex
        ob = self._outline_bgr
        return (
            "[Script Info]\n"
            "Title: ReLive Danmaku\n"
            "ScriptType: v4.00+\n"
            "Collisions: Normal\n"
            f"PlayResX: {self.width}\n"
            f"PlayResY: {self.height}\n"
            "Timer: 100.0000\n"
            "\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: R2L,{self.font},{self.fontsize},"
            f"&H{oh}FFFFFF,&H{oh}000000,"
            f"&H{oh}{ob},&H4F0000FF,"
            f"-1,0,0,0,100,100,0,0,1,{self.outline_size},0,1,0,0,0,0\n"
            "\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )

    def format_dialogue(self, item: DanmakuItem) -> str:
        y = self.dst + self.fontsize + (self.fontsize + self.margin_h) * item.track_id
        bgr_color = _rgb2bgr(item.color)
        start = _format_time(item.time_s)
        end = _format_time(item.time_s + self.duration)

        return (
            f"Dialogue: 0,{start},{end},"
            f"R2L,,0,0,0,,"
            f"{{\\move({self.width},{y},{-item.text_width},{y})}}"
            f"{{\\alpha&H{self._opacity_hex}&\\1c&H{bgr_color}&}}"
            f"{item.text}"
        )

    def write(self, output_path: str):
        lines = [self._build_header()]
        for item in self._items:
            lines.append(self.format_dialogue(item))
        lines.append("")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(lines))

    def open_live(self, output_path: str):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        self._live_fh = open(output_path, "w", encoding="utf-8",
                             newline="", buffering=1)  # line-buffered
        self._live_fh.write(self._build_header())
        self._live_path = output_path
        self._items.clear()
        self.reset_tracks()

    def append_to_file(self, item: DanmakuItem, output_path: str = ""):
        fh = self._live_fh
        if not fh:
            return
        fh.write(self.format_dialogue(item) + "\n")

    def reset_tracks(self):
        for track in self._tracks:
            track.last_item = None
            track.last_text_width = 0
        self._items.clear()

    def close(self):
        if self._live_fh:
            self._live_fh.close()
            self._live_fh = None
        self._live_path = ""


def danmaku_to_ass(ndjson_path: str, start_time_ms: float, output_path: str,
                   width: int = 1920, height: int = 1080,
                   time_start: float = 0.0, time_end: float = 0.0, **kwargs) -> int:
    """从 NDJSON 文件生成 ASS（离线），返回弹幕数

    Args:
        ndjson_path: NDJSON 文件路径
        start_time_ms: 录制起点毫秒时间戳
        output_path: 输出 ASS 路径
        time_start: 过滤起始秒数（0 = 不限）
        time_end: 过滤结束秒数（0 = 不限）
    """
    text = Path(ndjson_path).read_text(encoding="utf-8")
    if not text.strip():
        return 0

    danmaku_data = [json.loads(line) for line in text.splitlines() if line.strip()]
    chat_msgs = [d for d in danmaku_data if d.get("msg_type") == "chat"]

    writer = AssWriter(width=width, height=height, **kwargs)

    for item in chat_msgs:
        time_s = (item.get("timestamp_ms", 0) - start_time_ms) / 1000.0
        if time_s < 0:
            continue
        if time_start > 0 and time_s < time_start:
            continue
        if time_end > 0 and time_s >= time_end:
            continue
        writer.add(
            time_s=time_s - time_start,
            text=item.get("content", ""),
            color=item.get("color", "ffffff"),
        )

    writer.write(output_path)
    return len(writer._items)
