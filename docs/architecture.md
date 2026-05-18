# 架构设计

## 整体架构

ReLive 采用 **录制-缓存-播放-弹幕** 四层分离架构，代码按层组织在 `src/` 包中：

```
src/
├── config.py / logger.py          # 基础设施
├── recording/                     # 录制层
├── playback/                      # 播放 + 缓存层
├── danmaku/                       # 弹幕层
└── ui/                            # UI 层
```

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

### HLS 录制 — append_list 断线保护 + 2h 滑动窗口

FFmpeg 使用 `-f hls -hls_flags append_list+delete_segments -hls_list_size 1800`：
- `append_list`：重连时**追加**到已有 m3u8，不截断历史分段
- `hls_list_size 1800`：保留最近 1800 段（≈ 2 小时滑动窗口）
- `delete_segments`：FFmpeg 自动删除超出窗口的旧 TS 文件
- m3u8 和 `.ts` 文件都在 `cache/videos/` 目录下，路径一致

### 冻结式 DVR 回放

进入 DVR 模式时：
1. 从内存 `self.segments` 构建 VOD 格式的 `snapshot.m3u8`
2. snapshot 与 segment 文件在同一目录（`cache/videos/`），mpv 直接解析
3. DVR 内部拖动只调 `mpv.seek()`，不重建快照，不重创播放器
4. snapshot 生命周期：创建一次 → 整个回看会话使用

```
LIVE→DVR:   write_snapshot() → reinitialize(snapshot, seek_to)
DVR→DVR:    mpv.seek(new_pos)   ← 只 seek，不动 snapshot
DVR→LIVE:   reinitialize(live_url, sub_file=live.ass) → 重置弹幕时间基准
```

## 模块职责

### src/recording/ — 录制层

**stream_url.py** — 流 URL 获取
- 查询斗鱼房间开播状态（`/betard/{room_id}`）
- 获取播放地址（`/lapi/live/getH5Play/`）
- MD5 多轮签名认证
- 通过代理（HTTP_PROXY）请求

**ffmpeg_recorder.py** — HLS 录制
- 管理 FFmpeg 子进程（`-f hls -hls_flags append_list+delete_segments -hls_list_size 1800`）
- 输出 `cache/videos/playlist.m3u8` + `cache/videos/%Y%m%d_%H%M%S.ts`
- 实时监控 `waitTime` 延迟信息（`-debug ts_read`）
- 健康检查：进程存活 + m3u8 mtime 变化检测（30s stall timeout）
- stderr 后台线程采集，写入 `cache/ffmpeg_stderr.log`
- 进程树清理（Windows `taskkill /T`）
- 自动重连：死亡 → 等 5s → 获取新 URL → restart（append 保留旧分段）

### src/playback/ — 播放 + 缓存层

**cache_manager.py** — 缓存管理
- 每 3 秒解析 FFmpeg 的 `playlist.m3u8`，文件大小快速路径跳过无变化轮次
- 维护 `Segment` 列表（文件名、绝对路径、索引、时长）
- `total_duration()` 使用缓存值（segment 变化时更新，O(1) 读取）
- `find_segment_at(offset)` — 时间偏移定位 segment
- `get_absolute_time(seg_idx, offset)` — 计算绝对时间偏移
- `write_snapshot()` — 从内存 `self.segments` 构建 VOD 快照（不读磁盘）
- 不再做逐段 `os.path.exists()` 检查（FFmpeg HLS 保证 m3u8 条目对应已存在文件）

**video_player.py** — mpv 播放器
- 封装 `python-mpv`，通过 `wid` 嵌入 PyQt6 QWidget
- 33ms 定时轮询 position/duration
- seek 等待机制：等 mpv 解析完播放列表再执行跳转
  - 条件1：duration >= 预期时长的 50%
  - 条件2：duration 连续 3 次轮询稳定（±0.01s）
- `reinitialize()` — 销毁旧实例 + 创建新实例 + 可选 sub_file + play
- `set_sub_file()` — 动态加载字幕文件
- `sub_reload()` — 触发 mpv 重新加载字幕（每秒调用）

### src/danmaku/ — 弹幕层

**douyu_worker.js** — Node.js 子进程
- WebSocket 连接斗鱼弹幕代理（端口 8501-8506）
- STT 协议编解码（二进制包头 + key-value 序列化）
- 输出 JSON 行到 stdout，stderr 记录日志
- 45s 心跳 + 自动重连（最多 20 次）

**collector.py** — 进程管理
- 管理 Node.js 子进程生命周期
- 后台线程逐行读取 stdout，解析 JSON
- 跨线程 Qt 信号发射 `message_received`

**manager.py** — 数据管理
- 时间戳转换（绝对 ms → 相对秒）
- `offset_sec < 0` 过滤（消息早于录制开始则跳过）
- NDJSON 持久化（`cache/danmaku/session_<ts>.ndjson`）
- `danmaku_added` 信号 → `_on_danmaku_live`
- NDJSON 由 cache timer 定期 flush（~3s），不逐条 fsync
- 不保留内存中的弹幕列表（仅用 `_count` 计数器）

**ass_writer.py** — ASS 字幕生成
- 弹幕碰撞检测：多条弹幕分轨显示，避免重叠
- 轨道数 = `(height × dmrate) / (fontsize + margin)`
- `open_live()` — 行缓冲模式打开文件句柄，不反复 open/close
- `append_to_file()` — 直接写入已打开句柄，行缓冲自动 flush
- `danmaku_to_ass()` — 从 NDJSON 生成完整 ASS 文件（DVR 使用）
- `open_live()` 时清空 `_items` 和 collision track 状态

**ass_exporter.py** — 切片导出
- `export_clip_ass()` — 从 NDJSON 生成指定时间范围的 ASS

**renderer.py** — 视频渲染
- `render_with_fallback()` — 依次尝试 h264_amf / h264_nvenc / h264 编码器

### src/ui/ — UI 层

**main_window.py** — 主窗口
- 连接/断开直播流（双流架构）
- DVR 进度条交互（LIVE→DVR / DVR→DVR / DVR→LIVE）
- 入点/出点标记 + MP4 导出到命名文件夹
- 导出状态栏常驻（`_exporting` 标志抑制缓存信息覆盖）
- 导出完成后清理入点/出点（`_finish_export`）
- 自动重连（FFmpeg 死亡 → 获取新 URL → 重启录制）
- 弹幕协调：`_danmaku_live_start` 时间基准管理

**slider.py** — 自定义进度条
- `SeekSlider(QSlider)` — 点击直接跳转 + 入出点标记线绘制

**workers.py** — 后台线程
- `StreamWorker` — 后台获取直播流 URL
- `ExportWorker` — FFmpeg concat 流复制导出
- `RenderWorker` — FFmpeg 弹幕烧录到视频

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

## 性能优化摘要

已实施的优化（详见 `docs/performance.md`）：
- `total_duration()` 缓存，消除每秒 30 次 O(n) 求和
- ASS 文件行缓冲句柄复用，消除每条弹幕 open/close
- NDJSON 定期 flush，消除逐条 fsync
- 移除冗余 `os.path.exists()` 检查
- 删除无用的内存弹幕存储
- `write_snapshot()` 从内存构建，不读磁盘
- `open_live()` 清空旧弹幕对象防止泄漏
