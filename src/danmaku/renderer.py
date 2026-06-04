"""弹幕视频渲染器 — 用 FFmpeg subtitles 滤镜将 ASS 烧录到视频"""
import os
import platform
import subprocess
import time as _time
from datetime import datetime, timezone
from typing import Callable, Optional

from src.logger import get as _log
from src import config

log = _log("renderer")

# Callback: (current_time_seconds, speed_float) -> cancel (True = stop rendering)
ProgressCallback = Callable[[float, float], bool]
# Callback: () -> bool, True if the caller wants to cancel before trying next encoder
CancelCheck = Callable[[], bool]

# Per-encoder preset mapping for RENDER_HW_QUALITY config.
# Each encoder has its own preset naming scheme; this maps fast/balanced/slow
# to the correct argument list for each.
_HW_NVENC = {"fast": "p2", "balanced": "p4", "slow": "p7",}
_HW_QSV   = {"fast": "fast", "balanced": "medium", "slow": "slow"}
_HW_AMF   = {"fast": "speed", "balanced": "balanced", "slow": "quality"}


def _hw_args(encoder: str) -> list[str]:
    """Return quality/speed args for a hardware encoder based on RENDER_HW_QUALITY."""
    q = config.RENDER_HW_QUALITY
    if encoder == "h264_nvenc":
        return ["-preset", _HW_NVENC.get(q, "p4")]
    if encoder == "h264_qsv":
        return ["-preset", _HW_QSV.get(q, "medium")]
    if encoder == "h264_amf":
        return ["-quality", _HW_AMF.get(q, "balanced")]
    return []


def _encoder_args(encoder: str) -> list[str]:
    """Return FFmpeg args for *encoder* from current config values.

    Called at render time so config.json changes take effect immediately.
    """
    if encoder in ("libx264"):
        return [
            "-preset", config.RENDER_PRESET,
            "-crf", str(config.RENDER_CRF),
        ]
    if encoder == "h264_nvenc":
        return [
            *_hw_args(encoder),
            "-cq", str(config.RENDER_CRF),
            "-rc", "vbr",
        ]
    if encoder == "h264_amf":
        return [
            *_hw_args(encoder),
            "-rc", "cqp",
            "-qp_i", str(config.RENDER_CRF),
            "-qp_p", str(config.RENDER_CRF),
        ]
    if encoder == "h264_qsv":
        return [
            *_hw_args(encoder),
            "-global_quality", str(config.RENDER_CRF),
        ]
    if encoder == "h264_videotoolbox":
        return ["-q:v", "65"]
    if encoder == "h264_vaapi":
        return ["-qp", str(config.RENDER_CRF)]
    # Unknown encoder: use bitrate fallback
    return ["-b:v", config.RENDER_BITRATE]


def get_video_duration(video_path: str) -> float:
    """Return video duration in seconds using ffprobe, or 0.0 on failure."""
    try:
        result = subprocess.run(
            [config.FFPROBE_PATH, "-v", "error",
             "-show_entries", "format=duration",
             "-of", "csv=p=0", video_path],
            capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0:
            return float(result.stdout.decode().strip())
    except Exception:
        pass
    return 0.0


def _escape_ass_path(ass_path: str) -> str:
    """转义 ASS 路径供 FFmpeg subtitles 滤镜使用"""
    p = ass_path
    if platform.system().lower() == "windows":
        p = p.replace("\\", "/").replace(":/", "\\:/")
    return p


def _kill_ffmpeg(proc: subprocess.Popen) -> None:
    """Kill an FFmpeg process tree. Windows: taskkill /T; else: SIGKILL."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception:
            proc.kill()
    else:
        proc.kill()


def _cleanup_file(path: str) -> None:
    """Remove a file if it exists, ignoring errors."""
    try:
        if os.path.exists(path):
            os.remove(path)
            log.info("[RENDER] cleaned up partial output: %s", path)
    except OSError:
        pass


def _log_write(log_path: Optional[str], msg: str) -> None:
    """Append a timestamped line to the export log file."""
    if not log_path:
        return
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{ts}  {msg}\n")
    except OSError:
        pass

def get_video_fps(path: str) -> float:
    result = subprocess.run(
        [
            config.FFPROBE_PATH,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=avg_frame_rate",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    fps_str = result.stdout.strip()

    if fps_str == "0/0":
        return 30.0
    
    num, den = result.stdout.strip().split("/")
    return float(num) / float(den)

def render_with_danmaku(
    video_path: str,
    ass_path: str,
    output_path: str,
    encoder: str = "libx264",
    on_progress: Optional[ProgressCallback] = None,
    log_path: Optional[str] = None,
) -> bool:
    """将 ASS 弹幕烧录到视频中

    Args:
        video_path: 输入视频路径
        ass_path: ASS 字幕路径
        output_path: 输出视频路径
        encoder: 视频编码器 (参数来自 ENCODER_ARGS 字典)
        on_progress: 进度回调 (time_sec, speed) -> cancel.
                     返回 True 取消渲染，进程会被终止、不完整文件会被删除。
        log_path: 导出日志文件路径，不为 None 时所有 FFmpeg stderr 和事件都写入

    Returns:
        是否成功
    """
    ass_escaped = _escape_ass_path(ass_path)

    # video_fps = get_video_fps(video_path)

    cmd = [
        config.FFMPEG_PATH, "-y",
        "-i", video_path,
        "-vf", f"subtitles='{ass_escaped}'",
        "-c:v", encoder,
        *_encoder_args(encoder),
        "-c:a", "aac",
        "-b:a", config.RENDER_AUDIO_BITRATE,
        "-af", "aresample=async=1:first_pts=0",
        "-movflags", "+faststart",
        "-progress", "pipe:2",
        "-nostats",
        output_path,
    ]

    _log_write(log_path, f"[RENDER] 编码器: {encoder}")
    _log_write(log_path, f"[RENDER] ffmpeg {' '.join(cmd)}")
    log.info("[RENDER] cmd: %s", " ".join(cmd))

    t0 = _time.monotonic()

    proc = None
    try:
        proc = subprocess.Popen(
            cmd,

            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,   # progress
            stderr=subprocess.PIPE,   # ffmpeg logs

            text=True,
            errors="replace",

            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        _log_write(log_path, f"[RENDER] 启动 FFmpeg 失败: {e}")
        log.error("[RENDER] failed to start FFmpeg: %s", e)
        return False

    cancelled = False
    try:
        current_time = 0.0
        speed = 0.0

        for line in proc.stderr:
            line = line.strip()

            # Write every stderr line to export log
            if line and log_path:
                _log_write(log_path, f"  {line}")

            if line.startswith("out_time_ms="):
                try:
                    current_time = int(line.split("=", 1)[1]) / 1_000_000
                except (ValueError, IndexError):
                    pass

            elif line.startswith("speed="):
                try:
                    val = line.split("=", 1)[1].rstrip("x")
                    speed = float(val)
                except (ValueError, IndexError):
                    pass

            elif line == "progress=continue":
                if on_progress is not None and on_progress(current_time, speed):
                    cancelled = True
                    break

    except Exception as e:
        _log_write(log_path, f"[RENDER] stderr read error: {e}")
        log.error("[RENDER] error reading stderr: %s", e)
        cancelled = True

    elapsed = _time.monotonic() - t0

    if cancelled:
        _log_write(log_path, f"[RENDER] 用户取消 (耗时 {elapsed:.1f}s)")
        log.info("[RENDER] cancelled, killing process")
        _kill_ffmpeg(proc)
        _cleanup_file(output_path)
        return False

    returncode = proc.wait()

    if returncode != 0:
        _log_write(log_path, f"[RENDER] 失败 exit={returncode} 耗时 {elapsed:.1f}s")
        log.error("[RENDER] failed (exit=%d)", returncode)
        _cleanup_file(output_path)
        return False

    _log_write(log_path, f"[RENDER] 完成 exit=0 耗时 {elapsed:.1f}s")
    log.info("[RENDER] done: %s", output_path)
    return True


def render_with_fallback(
    video_path: str,
    ass_path: str,
    output_path: str,
    on_progress: Optional[ProgressCallback] = None,
    is_cancelled: Optional[CancelCheck] = None,
    log_path: Optional[str] = None,
) -> bool:
    """尝试多个编码器，依次回退。每个编码器从 ENCODER_ARGS 取参数模板。"""
    encoders = [
        "h264_nvenc",        # NVIDIA HW
        "h264_amf",          # AMD Windows
        "libx264",           # CPU fallback (CRF+preset)
    ]
    total = len(encoders)
    for i, enc in enumerate(encoders, 1):
        if is_cancelled is not None and is_cancelled():
            _log_write(log_path, f"[RENDER] 用户取消 (编码器 {i}/{total}: {enc} 未尝试)")
            log.info("[RENDER] cancelled before trying encoder: %s", enc)
            return False

        _log_write(log_path, f"[RENDER] 尝试编码器 {i}/{total}: {enc}")
        log.info("[RENDER] trying encoder: %s", enc)
        if render_with_danmaku(
            video_path, ass_path, output_path,
            encoder=enc, on_progress=on_progress, log_path=log_path,
        ):
            return True
        _log_write(log_path, f"[RENDER] {enc} 失败，尝试下一个")
        log.warning("[RENDER] encoder %s failed, trying next", enc)
    return False
