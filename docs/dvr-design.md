# DVR 回放设计

## 核心问题

直播 HLS 是持续增长的播放列表。直接 seek 到历史时间点，mpv 可能只看到部分播放列表，导致 duration 不准、seek 失败。

## 解决方案：冻结快照

进入 DVR 模式时，直接克隆 FFmpeg 的 `playlist.m3u8`，插入 VOD 标签：

```m3u8
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-TARGETDURATION:6
#EXT-X-MEDIA-SEQUENCE:0
#EXTINF:4.167000,
20260520_010002_000001.ts
...
#EXT-X-ENDLIST
```

关键设计：
- snapshot 与 segment 文件在同一目录（`cache/videos/`），裸文件名直接解析
- 直接克隆 FFmpeg 的 m3u8，不重建，路径不变
- `#EXT-X-PLAYLIST-TYPE:VOD` — mpv 当完整视频处理
- `#EXT-X-ENDLIST` — 播放列表结束
- 原子写入（tempfile + os.replace）

## HLS 滑动窗口

FFmpeg 使用 `-f hls -hls_flags append_list -hls_list_size N`：

| 参数 | 作用 |
|------|------|
| `-hls_flags append_list` | 重连时追加到已有 m3u8 |
| `-hls_list_size` | 保留最近 N 段（从 CACHE_HOURS/SEGMENT_SEC 计算） |
| `-hls_time 4` | 每 4 秒一个分段 |
| `-hls_segment_filename {session}_%06d.ts` | 会话前缀 + 序号，无碰撞 |

孤儿 TS 由 Python 定期清理：文件名自然排序，比 `_first_ts` 小的删除。

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

1. `CacheManager.write_snapshot()` 克隆 `playlist.m3u8`
2. 锁定 slider range 为 snapshot 总时长
3. `danmaku_to_ass(ndjson)` 从 NDJSON 文件生成 DVR 弹幕 ASS
4. `VideoPlayer.reinitialize(snapshot, seek_to)` — 回看模式（缓存+seekable）
5. `VideoPlayer.set_sub_file(dvr.ass)` 加载回看弹幕

### DVR→DVR（回看内部拖动）

1. `VideoPlayer.seek(new_pos)` — 只调 mpv seek
2. 不重建 snapshot，不重创播放器

### DVR→LIVE（回到直播）

1. 获取新直播流 URL（token 过期需刷新）
2. `_danmaku_live_start = time.time()` — 重置弹幕时间基准
3. `AssWriter.open_live(live.ass)` — 行缓冲模式写入 ASS header
4. `VideoPlayer.reinitialize(url, sub_file=live.ass)` — 直播模式（零缓冲低延迟）

## mpv 双模式配置

| 配置 | 直播 | 回看 |
|------|------|------|
| `cache` | `no` | `yes` |
| `cache_secs` | - | `10` |
| `force_seekable` | no | `yes` |
| `hr_seek` | no | `yes` |
| `demuxer_max_{back_}bytes` | `0` | `auto` |

## 弹幕时间线

两条独立时间基准：

| 基准 | 来源 | 重置时机 |
|------|------|----------|
| `start_time` (NDJSON) | 录制起点 wall clock | 从不 |
| `_danmaku_live_start` (ASS) | 直播播放起点 | 每次 LIVE/DVR→LIVE |

DVR ASS 从 NDJSON 文件生成：
```python
time_s = (timestamp_ms - start_time_ms) / 1000
out_time = time_s - mark_in_sec + time_offset
```
