"""切片弹幕 ASS 导出 — 从 NDJSON 生成指定时间范围的 ASS 字幕文件"""
import json
from pathlib import Path

from src import config
from src.danmaku.ass_writer import AssWriter


def export_clip_ass(ndjson_path: str, start_time_ms: float,
                    mark_in_sec: float, mark_out_sec: float,
                    output_path: str, time_offset: float = -3,
                    width: int = 1280, height: int = 720,
                    base_offset_sec: float = 0.0, **kwargs) -> int:
    """从 NDJSON 生成切片范围内的 ASS 文件

    Args:
        ndjson_path: NDJSON 文件路径
        start_time_ms: 录制起点毫秒时间戳
        mark_in_sec: 切片起始（播放器相对秒数）
        mark_out_sec: 切片结束（播放器相对秒数）
        output_path: 输出 ASS 路径
        time_offset: 弹幕时间偏移补偿（秒）
        width, height: 视频分辨率
        base_offset_sec: 缓存起始对应的绝对 offset_sec，用于转换播放器时间到 NDJSON 时间线

    Returns:
        匹配的弹幕数
    """
    text = Path(ndjson_path).read_text(encoding="utf-8")
    if not text.strip():
        return 0

    danmaku_data = [line for line in text.splitlines() if line.strip()]
    chat_msgs = []
    for line in danmaku_data:
        try:
            msg = json.loads(line)
            if msg.get("msg_type") == "chat":
                chat_msgs.append(msg)
        except Exception:
            continue

    scaled_fontsize = int(config.DANMAKU_FONT_SIZE * width / 1920)
    writer = AssWriter(width=width, height=height, fontsize=scaled_fontsize, **kwargs)

    abs_in = mark_in_sec + base_offset_sec
    abs_out = mark_out_sec + base_offset_sec

    items = []
    for msg in chat_msgs:
        offset_sec = msg.get("offset_sec", 0)
        if offset_sec < abs_in or offset_sec >= abs_out:
            continue

        out_time = offset_sec - abs_in + time_offset
        if out_time < 0:
            continue

        res = writer.add(
            time_s=out_time,
            text=msg.get("content", ""),
            color=msg.get("color", "ffffff"),
        )
        if res:
            items.append(res)

    writer.write(output_path, items)
    return len(items)
