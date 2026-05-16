# DVR 回放设计

## 核心问题

直播 HLS 是持续增长的播放列表，mpv 会不断请求新的 segment。直接 seek 到历史时间点，mpv 可能只看到部分播放列表，导致 duration 不准、seek 失败。

## 解决方案：冻结快照

进入 DVR 模式时，从 CacheManager 已解析的 segment 列表生成一个**静态 VOD 播放列表**：

```m3u8
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:7
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-PLAYLIST-TYPE:VOD
#EXTINF:4.167000,
000005.ts
#EXTINF:4.166000,
000006.ts
...
#EXT-X-ENDLIST
```

关键标记：
- `#EXT-X-PLAYLIST-TYPE:VOD` — mpv 当完整视频处理
- `#EXT-X-ENDLIST` — 播放列表结束，mpv 不会请求新 segment

## 状态机

```
                    拖动进度条
    ┌──────────┐ ──────────────▶ ┌──────────┐
    │   LIVE   │                 │   DVR    │
    │ 直播预览  │ ◀────────────── │ 回看模式  │
    └──────────┘   点「回到直播」  └──────────┘
                  或拖到进度条末端
```

### LIVE→DVR（首次进入回看）

1. `CacheManager.write_snapshot()` 生成 `snapshot.m3u8`
2. 锁定 slider range 为 snapshot 总时长
3. `VideoPlayer.reinitialize(snapshot, seek_to)` 加载快照 + 跳转

### DVR→DVR（回看内部拖动）

1. `VideoPlayer.seek(new_pos)` — 只调 mpv seek
2. 不重建 snapshot，不重创播放器，不更新 slider range

### DVR→LIVE（回到直播）

1. `VideoPlayer.reinitialize(live_url)` 重新播放直播 URL
2. 恢复 slider range 为实时缓存总时长
3. slider 自动跟踪末端

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
fd, tmp = tempfile.mkstemp(suffix=".tmp", dir=CACHE_DIR)
with os.fdopen(fd, "w") as f:
    f.writelines(lines)
    f.flush()
    os.fsync(f.fileno())
os.replace(tmp, SNAPSHOT_M3U8)  # 原子替换
```

## 已知限制

- snapshot 时长 = 进入 DVR 时的缓存时长，不会包含之后的新 segment
- seek 精度受关键帧位置影响，可能有 ~0.15s 偏移
- Windows 上 mpv 持有 snapshot 文件锁，退出 DVR 前不能删除
