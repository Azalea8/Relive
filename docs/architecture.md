# 架构设计

## 整体架构

ReLive 采用 **录制-缓存-播放** 三层分离架构：

```
录制层 (FFmpeg)     缓存层 (CacheManager)     播放层 (mpv)
─────────────────   ──────────────────────    ─────────────
直播流 → .ts 文件    解析 m3u8 → Segment 列表   播放 URL 或快照
持续运行              周期扫描                   嵌入 PyQt6
```

### 双流并行

直播模式下两路并行：
- **FFmpeg** 录制到本地 `cache/` 目录（HLS 分段）
- **mpv** 直接播放直播 URL（实时预览）

两者独立运行，互不干扰。

### 冻结式 DVR 回放

进入 DVR 模式时：
1. `CacheManager.write_snapshot()` 从当前 segment 列表生成冻结的 `snapshot.m3u8`
2. snapshot 包含 `#EXT-X-PLAYLIST-TYPE:VOD` + `#EXT-X-ENDLIST`，mpv 当普通视频处理
3. DVR 内部拖动只调 `mpv.seek()`，不重建快照，不重创播放器
4. snapshot 生命周期：创建一次 → 整个回看会话使用 → 退出回看时保留

```
LIVE→DVR:  write_snapshot() → reinitialize(snapshot, seek_to)
DVR→DVR:   mpv.seek(new_pos)   ← 只 seek，不动 snapshot
DVR→LIVE:  reinitialize(live_url)
```

## 模块职责

### stream_url.py — 流 URL 获取

- 查询斗鱼房间开播状态（`/betard/{room_id}`）
- 获取播放地址（`/lapi/live/getH5Play/`）
- MD5 多轮签名认证

### ffmpeg_recorder.py — HLS 录制

- 管理 FFmpeg 子进程（`-f hls` 输出）
- `hls_flags: delete_segments+append_list+omit_endlist`
- 健康检查：进程存活 + m3u8 mtime 变化检测
- stderr 后台线程采集，写入 `cache/ffmpeg_stderr.log`
- 进程树清理（Windows `taskkill /T`）

### cache_manager.py — 缓存管理

- 周期解析 FFmpeg 的 `playlist.m3u8`
- 维护 `Segment` 列表（文件名、路径、时长、索引）
- `find_segment_at(offset)` — 时间偏移定位 segment
- `write_snapshot()` — 生成冻结 VOD 快照（原子写入）

### video_player.py — mpv 播放器

- 封装 `python-mpv`，通过 `wid` 嵌入 PyQt6 QWidget
- 33ms 定时轮询 position/duration
- seek 等待机制：等 mpv 解析完 m3u8 再执行跳转
- `reinitialize()` — 销毁旧实例 + 创建新实例 + 播放

### main_window.py — 主窗口

- 连接/断开直播流
- DVR 进度条交互（LIVE→DVR / DVR→DVR / DVR→LIVE）
- 入点/出点标记 + MP4 导出（FFmpeg concat demuxer）
- 自动重连（FFmpeg 死亡 → 获取新 URL → 重启录制）
