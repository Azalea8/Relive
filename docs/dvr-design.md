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

## 弹幕密度曲线

进入 DVR 时从 NDJSON 读取弹幕数据，计算每 2 秒桶的聊天条数，在进度条上方绘制灰色平滑曲线（Catmull-Rom 样条 + 抗锯齿）。切回直播后清空。

## HLS 滑动窗口

FFmpeg 使用 `-f hls -hls_flags append_list -hls_list_size N`：

| 参数 | 作用 |
|------|------|
| `-hls_flags append_list` | 重连时追加到已有 m3u8 |
| `-hls_list_size` | 保留最近 N 段（从 CACHE_HOURS/SEGMENT_SEC 计算） |
| `-hls_time 4` | 每 4 秒一个分段 |
| `-hls_segment_filename {session}_%06d.ts` | 会话前缀 + 序号，无碰撞 |

## 状态机

```
                    拖动进度条
    ┌──────────┐ ──────────────▶ ┌──────────┐
    │   LIVE   │                 │   DVR    │
    │ 直播预览  │ ◀────────────── │ 回看模式  │
    │ + 直播弹幕│  点「回到直播」  │ + 密度曲线│
    └──────────┘  或拖到进度条末端 └──────────┘
```

### LIVE→DVR

1. `CacheManager.write_snapshot()` 克隆 `playlist.m3u8`
2. 锁定 slider range 为 snapshot 总时长
3. `get_density_buckets()` 读 NDJSON 生成密度曲线
4. `danmaku_to_ass(ndjson)` 生成 DVR 弹幕 ASS
5. `VideoPlayer.reinitialize(snapshot, seek_to)` — 回看模式（缓存+seekable）
6. `VideoPlayer.set_sub_file(dvr.ass)` 加载回看弹幕

### DVR→DVR

1. `VideoPlayer.seek(new_pos)` — 只调 mpv seek

### DVR→LIVE

1. 获取新直播流 URL
2. `_danmaku_live_start = time.time()` — 重置弹幕时间基准
3. `clear_density()` — 清空密度曲线
4. `AssWriter.open_live(live.ass)` — 写入 header
5. `VideoPlayer.reinitialize(url, sub_file=live.ass)` — 直播模式

## 全屏（直播专用）

- 弹幕和设置之间的全屏按钮，仅在直播模式可用
- 隐藏上下 chrome + 状态栏，仅显示视频
- ESC 退出全屏

## mpv 双模式配置

| 配置 | 直播 | 回看 |
|------|------|------|
| `cache` | `no` | `yes` |
| `cache_secs` | - | `10` |
| `force_seekable` | no | `yes` |
| `hr_seek` | no | `yes` |
