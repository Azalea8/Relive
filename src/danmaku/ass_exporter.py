"""切片弹幕 ASS 导出 — 从 NDJSON 生成指定时间范围的 ASS 字幕文件"""
import json
from pathlib import Path

from src.danmaku.ass_writer import AssWriter


def export_clip_ass(ndjson_path: str, start_time_ms: float,
                    mark_in_sec: float, mark_out_sec: float,
                    output_path: str, time_offset: float = -1.5,
                    width: int = 1920, height: int = 1080, **kwargs) -> int:
    """从 NDJSON 生成切片范围内的 ASS 文件

    Args:
        ndjson_path: NDJSON 文件路径
        start_time_ms: 录制起点毫秒时间戳
        mark_in_sec: 切片起始（缓存相对秒数）
        mark_out_sec: 切片结束（缓存相对秒数）
        output_path: 输出 ASS 路径
        time_offset: 弹幕时间偏移补偿（秒）
        width, height: 视频分辨率

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

    writer = AssWriter(width=width, height=height, **kwargs)

    items = []
    for msg in chat_msgs:
        offset_sec = msg.get("offset_sec", 0)
        if offset_sec < mark_in_sec or offset_sec >= mark_out_sec:
            continue

        out_time = offset_sec - mark_in_sec + time_offset
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
