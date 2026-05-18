# DVR 回放设计

## 核心问题

直播 HLS 是持续增长的播放列表。直接 seek 到历史时间点，mpv 可能只看到部分播放列表，导致 duration 不准、seek 失败。

## 解决方案：冻结快照

进入 DVR 模式时，从内存 segment 列表生成一个**静态 VOD 播放列表**（不读磁盘）：

```m3u8
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:6
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-PLAYLIST-TYPE:VOD
#EXTINF:4.167000,
20260517_214920.ts
#EXTINF:4.166000,
20260517_214930.ts
...
#EXT-X-ENDLIST
```

关键设计：
- snapshot 与 segment 文件在同一目录（`cache/videos/`），裸文件名直接解析
- `#EXT-X-PLAYLIST-TYPE:VOD` — mpv 当完整视频处理
- `#EXT-X-ENDLIST` — 播放列表结束，mpv 不会请求新 segment
- 快照从 `self.segments` 内存数据构建，不重复读取 m3u8 文件

## HLS 断线保护 + 2h 滑动窗口

FFmpeg 使用 `-f hls -hls_flags append_list+delete_segments -hls_list_size 1800`：

| 参数 | 作用 |
|------|------|
| `-hls_flags append_list` | 重连时**追加**到已有 m3u8，不截断 |
| `-hls_list_size 1800` | 保留最近 1800 段（≈ 2 小时滑动窗口） |
| `-hls_flags delete_segments` | 超出窗口的旧 TS 文件由 FFmpeg 自动删除 |
| `-hls_time 4` | 每 4 秒一个分段 |

重连流程：
```
FFmpeg #1 crashed → 旧 m3u8: [seg1, seg2, seg3]
  ↓
FFmpeg #2 started (append_list)
  ↓
m3u8: [seg1, seg2, seg3, seg4, seg5, ...]  ← 历史分段不丢失

... 达到 1800 段后 ...
  ↓
FFmpeg 自动删除最旧 segment，维持 1800 段滑动窗口
```

## 状态机

```
                    拖动进度条
    ┌──────────┐ ──────────────▶ ┌──────────┐
    │   LIVE   │                 │   DVR    │
    │ 直播预览  │ ◀────────────── │ 回看模式  │
    │ + 直播弹幕│  点「回到直播」  │ + 回看弹幕│
    └──────────┘  或拖到进度条末端 └──────────┘
```

### LIVE→DVR（首次进入回看）

1. `CacheManager.write_snapshot()` 从内存 segment 列表构建 `snapshot.m3u8`
2. 锁定 slider range 为 snapshot 总时长（`_dvr_frozen_duration`）
3. 停止弹幕 reload timer
4. `danmaku_to_ass(ndjson)` 从 NDJSON 文件生成 DVR 弹幕 ASS
5. `VideoPlayer.reinitialize(snapshot, seek_to)` 加载快照 + 跳转
6. `VideoPlayer.set_sub_file(dvr.ass)` 加载回看弹幕

### DVR→DVR（回看内部拖动）

1. `CacheManager.find_segment_at(target_sec)` — 定位目标 segment
2. `VideoPlayer.seek(new_pos)` — 只调 mpv seek
3. 不重建 snapshot，不重创播放器，不更新 slider range

### DVR→LIVE（回到直播）

1. 获取新直播流 URL（token 过期需刷新）
2. `_danmaku_live_start = time.time()` — 重置弹幕时间基准
3. `AssWriter.open_live(live.ass)` — 行缓冲模式写入 ASS header
4. `VideoPlayer.reinitialize(url, sub_file=live.ass)` — 加载字幕后播放
5. 启动弹幕 reload timer（每秒 sub-reload）
6. 恢复 slider range 为实时缓存总时长，slider 自动跟踪末端

## seek 等待机制

mpv 加载 m3u8 需要时间解析播放列表。seek 时机：

```python
# 两个条件，满足任一即执行 seek：
# 1. mpv 报告的 duration >= 预期时长的 50%
# 2. duration 连续 3 次轮询稳定（±0.01s）
threshold = max(expected_duration * 0.5, 2.0)
ready = dur >= threshold or stable_count >= 3
```

## 原子写入

snapshot.m3u8 使用原子写入避免损坏：

```python
fd, tmp = tempfile.mkstemp(suffix=".tmp", dir=SEGMENT_DIR)
with os.fdopen(fd, "w") as f:
    f.writelines(lines)
    f.flush()
    os.fsync(f.fileno())
os.replace(tmp, SNAPSHOT_M3U8)  # 原子替换
```

## 弹幕时间线

两条独立时间基准：

| 基准 | 来源 | 重置时机 |
|------|------|----------|
| `start_time` (NDJSON) | 录制起点 wall clock | 从不 |
| `_danmaku_live_start` (ASS) | 直播播放起点 | 每次 LIVE/DVR→LIVE |

DVR ASS 从 NDJSON 文件生成，不做内存缓存：
```python
time_s = (timestamp_ms - start_time_ms) / 1000
out_time = time_s - mark_in_sec + time_offset
```
