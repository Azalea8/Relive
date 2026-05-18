"""弹幕视频渲染器 — 用 FFmpeg subtitles 滤镜将 ASS 烧录到视频"""
import platform
import subprocess

from src.logger import get as _log
from src import config

log = _log("renderer")


def _escape_ass_path(ass_path: str) -> str:
    """转义 ASS 路径供 FFmpeg subtitles 滤镜使用"""
    p = ass_path
    if platform.system().lower() == "windows":
        p = p.replace("\\", "/").replace(":/", "\\:/")
    return p


def render_with_danmaku(video_path: str, ass_path: str, output_path: str,
                        encoder: str = "h264") -> bool:
    """将 ASS 弹幕烧录到视频中

    Args:
        video_path: 输入视频路径
        ass_path: ASS 字幕路径
        output_path: 输出视频路径
        encoder: 视频编码器

    Returns:
        是否成功
    """
    ass_escaped = _escape_ass_path(ass_path)

    cmd = [
        config.FFMPEG_PATH, "-y",
        "-hwaccel", "auto",
        "-i", video_path,
        "-vf", f"subtitles='{ass_escaped}'",
        "-c:v", encoder,
        "-b:v", "15M",
        "-c:a", "aac",
        "-b:a", "320K",
        "-movflags", "+faststart",
        output_path,
    ]

    log.info("[RENDER] cmd: %s", " ".join(cmd))

    result = subprocess.run(
        cmd, capture_output=True, timeout=600,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="ignore")[-1000:]
        log.error("[RENDER] failed: %s", err)
        return False

    log.info("[RENDER] done: %s", output_path)
    return True


def render_with_fallback(video_path: str, ass_path: str, output_path: str) -> bool:
    """尝试多个编码器，依次回退: h264_amf -> h264_nvenc -> h264"""
    encoders = ["h264_amf", "h264_nvenc", "h264"]
    for enc in encoders:
        log.info("[RENDER] trying encoder: %s", enc)
        if render_with_danmaku(video_path, ass_path, output_path, encoder=enc):
            return True
        log.warning("[RENDER] encoder %s failed, trying next", enc)
    return False
