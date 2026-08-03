"""CLI 弹幕测试工具 — 连接直播间，打印过滤后的弹幕到控制台。
用法: python test_danmaku.py <platform> <room_id>
示例: python test_danmaku.py douyu 12306
"""
import signal
import sys
import time

from PySide6.QtCore import QCoreApplication, QTimer

from src.danmaku.collector import DanmakuCollector
from src.danmaku.manager import DanmakuManager
from src.danmaku.filter import DanmakuFilter
from src import config

if len(sys.argv) < 3:
    print("用法: python test_danmaku.py <douyu|douyin> <room_id>")
    sys.exit(1)

platform = sys.argv[1]
room_id = sys.argv[2]

if platform not in ("douyu", "douyin"):
    print(f"不支持的平台: {platform} (仅 douyu, douyin)")
    sys.exit(1)

app = QCoreApplication(sys.argv)

danmaku_filter = DanmakuFilter(config.DANMAKU_FILTER_PATH)
print(f"加载了 {danmaku_filter.rule_count} 条过滤规则")
if danmaku_filter.rules:
    for r in danmaku_filter.rules:
        print(f"  - {r}")

total = 0
blocked = 0

# Wrap filter to track blocked messages without modifying core
_orig_is_blocked = danmaku_filter.is_blocked


def _tracked_is_blocked(text):
    global blocked
    result = _orig_is_blocked(text)
    if result:
        blocked += 1
        rule = danmaku_filter.matching_rule(text)
        print(f"[BLOCKED by '{rule}'] {text}")
    return result


danmaku_filter.is_blocked = _tracked_is_blocked

collector = DanmakuCollector()
manager = DanmakuManager()
manager.set_filter(danmaku_filter)


def on_message(msg):
    global total
    total += 1
    print(f"[{msg.get('msg_type', '?')}] {msg.get('username', '')}: {msg.get('content', '')}")


manager.danmaku_added.connect(on_message)
collector.message_received.connect(manager.on_raw_message)

manager.set_recording_start(time.time())

print(f"连接 {platform} 房间 {room_id}，等待弹幕... (Ctrl+C 退出)")
collector.start(room_id, platform)

quit_flag = False


def _on_signal(*_):
    global quit_flag
    quit_flag = True


signal.signal(signal.SIGINT, _on_signal)
signal.signal(signal.SIGTERM, _on_signal)

timer = QTimer()
timer.timeout.connect(lambda: app.quit() if quit_flag else None)
timer.start(200)

app.exec()
timer.stop()
collector.stop()
print(f"\n共 {total} 条弹幕，过滤了 {blocked} 条")
