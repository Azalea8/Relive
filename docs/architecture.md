# 架构设计

## 整体架构

ReLive 采用 **录制-缓存-播放-弹幕** 四层分离架构：

```
录制层 (FFmpeg)      缓存层 (CacheManager)     播放层 (mpv)      弹幕层 (Danmaku)
──────────────────   ──────────────────────    ─────────────    ────────────────
直播流 → .ts + .m3u8  解析 m3u8 → Segment 列表   播放 URL 或快照   采集+展示+录制
持续运行              周期扫描                  嵌入 PyQt6        Node.js 子进程
```

### 双流并行

直播模式下两路独立运行：
- **FFmpeg** 录制到本地 `cache/videos/` 目录（HLS 分段 + m3u8）
- **mpv** 直接播放直播 URL（实时预览 + 弹幕 ASS 字幕）

### HLS 录制 — append_list 断线保护

FFmpeg 使用 `-f hls -hls_flags append_list -hls_list_size 0`：
- `append_list`：重连时**追加**到已有 m3u8，不截断历史分段
- `hls_list_size 0`：保留全部分段（最多 2 小时缓存）
- m3u8 和 `.ts` 文件都在 `cache/videos/` 目录下，路径一致

### 冻结式 DVR 回放

进入 DVR 模式时：
1. 从 FFmpeg 的 `playlist.m3u8` 复制 → 添加 `#EXT-X-PLAYLIST-TYPE:VOD` + `#EXT-X-ENDLIST` → 另存为 `snapshot.m3u8`
2. `snapshot.m3u8` 与 segment 文件在同一目录，mpv 直接解析
3. DVR 内部拖动只调 `mpv.seek()`，不重建快照，不重创播放器
4. snapshot 生命周期：创建一次 → 整个回看会话使用

```
LIVE→DVR:   write_snapshot() → reinitialize(snapshot, seek_to)
DVR→DVR:    mpv.seek(new_pos)   ← 只 seek，不动 snapshot
DVR→LIVE:   reinitialize(live_url, sub_file=live.ass) → 重置弹幕时间基准
```

## 模块职责

### stream_url.py — 流 URL 获取

- 查询斗鱼房间开播状态（`/betard/{room_id}`）
- 获取播放地址（`/lapi/live/getH5Play/`）
- MD5 多轮签名认证
- 通过代理（HTTP_PROXY）请求

### ffmpeg_recorder.py — HLS 录制

- 管理 FFmpeg 子进程（`-f hls -hls_flags append_list -hls_list_size 0`）
- 输出 `cache/videos/playlist.m3u8` + `cache/videos/%Y%m%d_%H%M%S.ts`
- 健康检查：进程存活 + m3u8 mtime 变化检测（30s stall timeout）
- stderr 后台线程采集，写入 `cache/ffmpeg_stderr.log`
- 进程树清理（Windows `taskkill /T`）
- 自动重连：死亡 → 等 5s → 获取新 URL → restart（append 保留旧分段）

### cache_manager.py — 缓存管理

- 每 3 秒解析 FFmpeg 的 `playlist.m3u8`
- 维护 `Segment` 列表（文件名、绝对路径、索引、时长）
- `find_segment_at(offset)` — 时间偏移定位 segment（线性扫描）
- `get_absolute_time(seg_idx, offset)` — 计算绝对时间偏移
- `write_snapshot()` — 从直播 m3u8 复制并添加 VOD 标签，生成冻结快照
- 快速路径：文件大小未变则跳过解析；文件名+时长均不变则跳过更新

### video_player.py — mpv 播放器

- 封装 `python-mpv`，通过 `wid` 嵌入 PyQt6 QWidget
- 33ms 定时轮询 position/duration
- seek 等待机制：等 mpv 解析完播放列表再执行跳转
  - 条件1：duration >= 预期时长的 50%
  - 条件2：duration 连续 3 次轮询稳定（±0.01s）
- `reinitialize()` — 销毁旧实例 + 创建新实例 + 可选 sub_file + play
- `set_sub_file()` — 动态加载字幕文件
- `sub_reload()` — 触发 mpv 重新加载字幕（每秒调用）

### main_window.py — 主窗口

- 连接/断开直播流（双流架构）
- DVR 进度条交互（LIVE→DVR / DVR→DVR / DVR→LIVE）
- 入点/出点标记 + MP4 导出到命名文件夹
- 导出状态栏常驻（`_exporting` 标志抑制缓存信息覆盖）
- 导出完成后清理入点/出点
- 自动重连（FFmpeg 死亡 → 获取新 URL → 重启录制）
- 弹幕协调：`_danmaku_live_start` 时间基准管理

### 弹幕模块 — core/danmaku/

**douyu_worker.js** — Node.js 子进程
- WebSocket 连接斗鱼弹幕代理（端口 8501-8506）
- STT 协议编解码（二进制包头 + key-value 序列化）
- 输出 JSON 行到 stdout，stderr 记录日志
- 45s 心跳 + 自动重连（最多 20 次）

**danmaku_collector.py** — 进程管理
- 管理 Node.js 子进程生命周期
- 后台线程逐行读取 stdout，解析 JSON
- 跨线程 Qt 信号发射 `message_received`

**danmaku_manager.py** — 数据管理
- 时间戳转换（绝对 ms → 相对秒）
- `offset_sec < 0` 过滤（消息早于录制开始则跳过）
- NDJSON 持久化（`cache/danmaku/session_<ts>.ndjson`）
- `bisect` 二分查询指定时间范围的弹幕
- `danmaku_added` 信号 → `_on_danmaku_live`

**ass_writer.py** — ASS 字幕生成
- 弹幕碰撞检测：多条弹幕分轨显示，避免重叠
- 轨道数 = `(height × dmrate) / (fontsize + margin)`
- `open_live()` — 写入 ASS header，设置 `_live_path`
- `append_to_file()` — 每条弹幕打开文件追加写入
- `danmaku_to_ass()` — 从 NDJSON 生成完整 ASS 文件（DVR 使用）

**ass_exporter.py** — 切片导出
- `export_clip_ass()` — 从 NDJSON 生成指定时间范围的 ASS

**dm_renderer.py** — 视频渲染
- `render_with_fallback()` — 依次尝试 h264_amf / h264_nvenc / h264 编码器

## 弹幕时间线设计

两个独立的时间基准，互不干扰：

| 基准 | 用途 | 设置时机 | 重置时机 |
|------|------|----------|----------|
| `start_time` | NDJSON 录播 | 初始连接一次 | 从不（会话级别） |
| `_danmaku_live_start` | 直播 ASS 显示 | 初始连接 / 切回直播 | 每次新直播播放 |

### 回看弹幕

DVR 的 `dvr.ass` 通过 `danmaku_to_ass()` 从 NDJSON 生成：
```
offset_sec = timestamp_ms/1000 - start_time
out_time = offset_sec - mark_in_sec + time_offset
```
时间戳始终相对 DVR 回看起点，匹配 mpv 的 snapshot 播放位置。

## 导出流程

```
设置入点/出点 → 点击「导出」→ 命名文件夹对话框
  → FFmpeg concat (流复制) → 生成 ASS → 渲染弹幕 → 完成
                                                 ↓
                                    _finish_export() 清理入出点
```
