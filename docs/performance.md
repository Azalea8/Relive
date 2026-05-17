# ReLive 性能审计报告

> 审计日期: 2026-05-18  
> 审计范围: Python + Node.js 全链路  
> 录制规格: 4s/segment, hls_list_size=0, 2 小时录制 ≈ 1800 segments

---

## 1. P0 — total_duration() 在 30fps 位置更新循环中 O(n) 求和

**位置**  
`cache_manager.py:61` → `main_window.py:811`

**现状**  
```python
# cache_manager.py
def total_duration(self) -> float:
    return sum(s.duration for s in self.segments)  # O(n)

# main_window.py _on_position_changed (30fps)
total = self._cache.total_duration()  # 每秒 30 次 O(n)
```

`position_changed` 信号由 `video_player.py` 的 33ms QTimer 驱动，约 30fps。每次调用都对全量 segments 做 sum。2 小时 1800 段 = 每秒 54,000 次浮点加法。

**影响**  
CPU 持续占用，帧率越高越严重。

**修复方向**  
在 `_parse_m3u8()` 替换 segments 时更新缓存值，`total_duration()` 直接返回缓存。

---

## 2. P1 — ASS 文件每条弹幕 open/close

**位置**  
`ass_writer.py:187-193`

**现状**  
```python
def append_to_file(self, item):
    with open(path, "a", ...) as f:   # open syscall
        f.write(dialogue + "\n")       # write syscall
                                       # close syscall (隐式)
```

每条弹幕触发 3 个系统调用（open + write + close）。热门直播间 50 条/秒 = 150 次 syscall/秒。

**影响**  
主线程文件 I/O 阻塞，弹幕高峰时 UI 卡顿。

**修复方向**  
`open_live()` 保持文件句柄打开（`self._live_fh`），`append_to_file()` 直接写入已打开句柄，定期 flush（每 10 条或 500ms）。

---

## 3. P1 — NDJSON 每条消息 flush()

**位置**  
`danmaku_manager.py:58-59`

**现状**  
```python
self._jsonl_file.write(json.dumps(msg, ensure_ascii=False) + "\n")
self._jsonl_file.flush()  # 每条都 fsync
```

**影响**  
每条弹幕强制磁盘同步，主线程阻塞。

**修复方向**  
去掉逐条 `flush()`，改为在独立 5s timer 中定期 flush。Python 文件对象自带 8KB 缓冲区，数据安全。

---

## 4. P1 — m3u8 扫描对全部 segments 做 os.path.exists()

**位置**  
`cache_manager.py:147`

**现状**  
```python
for line in lines:
    if not line.startswith("#"):
        path = os.path.normpath(os.path.join(SEGMENT_DIR, line))
        if not os.path.exists(path):  # 每个 segment 一次 stat
            continue
```

每 3 秒扫描一次，每次对全部 ~1800 segments 调用 `os.path.exists()`。虽然 FFmpeg 只追加不删除（`hls_list_size=0`），但代码每轮都检查所有历史文件。

**影响**  
每 3 秒约 1800 次不必要的 stat 调用。

**修复方向**  
用 `set` 记住上一轮已确认存在的 filename，仅对新 segment 做 `os.path.exists()`。

---

## 5. P2 — write_snapshot() 重复读取 m3u8 文件

**位置**  
`cache_manager.py:65-84`

**现状**  
`write_snapshot()` 重新从磁盘读取完整 m3u8，逐行解析后写入 snapshot。但 `self.segments` 已有解析好的数据。

**影响**  
每次 DVR seek 多一次文件 I/O。

**修复方向**  
直接从 `self.segments` 构建 VOD m3u8，省去读文件 + 重新解析。

---

## 6. P3 — _find_track() 每条弹幕 O(tracks) 扫描

**位置**  
`ass_writer.py:88-110`

**现状**  
每条弹幕遍历全部轨道做碰撞检测。1080p 约 25 tracks × 50 条/秒 = 可接受。

**修复方向**  
后续可考虑按过期时间排序轨道提前退出，或将文本宽度计算延迟到确定有轨道后。

---

## 7. P3 — DanmakuManager._messages 无限增长

**位置**  
`danmaku_manager.py:21-22`

**现状**  
`_messages` 和 `_offsets` 列表全程追加不缩减。2 小时约 60MB。

**影响**  
内存持续增长，可接受（2h = 60MB）。超长录制（8h+）需注意。

**修复方向**  
后续可添加基于时间的淘汰或上限。

---

## 8. P3 — 1 秒 sub-reload timer，mpv 重复解析整个 ASS 文件

**位置**  
`main_window.py:556`

**现状**  
每秒 `sub-reload` 让 mpv 重新读取+解析整个 `live.ass`。文件随时间增长（2h = 数万行），mpv 重新解析耗时增加。

**影响**  
早期可忽略，后期可能造成渲染帧时间峰值。

**修复方向**  
后续评估 mpv 的 sub-add/sub-remove API 是否支持增量更新。

---

## 9. 低优先级 / 观察项

| 项目 | 位置 | 说明 |
|------|------|------|
| mpv 33ms poll timer | `video_player.py:31` | 每次 poll 做 2 次 IPC 属性访问，60 次/秒。可接受 |
| `time.time()` 在热路径 | 每条弹幕 + 30fps 位置更新 | 微小的 syscall 开销 |
| JS re2 替换 | `douyu_worker.js:77` | 每次 STT 反序列化 2 次全局正则替换 |
| JS `catch(e){}` 静默丢弃 | `douyu_worker.js:154` | 解析错误无诊断 |
| 弹幕时间线分离 | `main_window.py` | `_danmaku_live_start` 相对直播播放起点，ndjson 用绝对 `start_time` |

---

## 已修复的性能问题

| 问题 | 状态 |
|------|------|
| `_on_go_live_url` AssWriter 创建两次 | **已修复** — 合并为一次，删除重复代码 |
| 切回直播弹幕时间戳不匹配 | **已修复** — `_danmaku_live_start` 每次 go-live 重置 |
| `_on_segments_changed` 覆盖导出状态栏 | **已修复** — `_exporting` 标志抑制 |

---

## 优化优先级总结

| 优先级 | 条目 | 预计收益 | 改动量 |
|--------|------|----------|--------|
| **P0** | 缓存 total_duration | 消除每秒 30 次 O(n) 求和 | 3 行 |
| **P1** | ASS 保持文件句柄打开 | 消除每条弹幕 open/close | ~15 行 |
| **P1** | NDJSON 去掉逐条 flush | 消除每秒数十次 fsync | ~5 行 |
| **P1** | 跳过已知 segment exists 检查 | 每 3s 省 1800 次 stat | ~10 行 |
| **P2** | write_snapshot 用内存数据 | 省去重复读文件+解析 | ~25 行 |
| **P3** | 其余优化项 | 边际收益 | 后续版本 |
