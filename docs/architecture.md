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
录制层 (FFmpeg)      缓存层 (CacheManager)     播放层 (mpv)       弹幕层 (Danmaku)
──────────────────   ──────────────────────    ─────────────    ────────────────
直播流 → .ts + .m3u8  扫描 m3u8 → 计数+时长      直播:低延迟/回看:缓存  采集+展示+NDJSON
持续运行             周期扫描                   嵌入 PyQt6        Node.js 子进程
```

### 双流并行

直播模式下两路独立运行：
- **FFmpeg** 录制到本地 `cache/videos/` 目录（HLS 分段 + m3u8）
- **mpv** 直接播放直播 URL（实时预览 + 弹幕 ASS 字幕）

### HLS 录制

FFmpeg 使用 `-f hls -hls_flags append_list -hls_list_size 1800`：
- `append_list`：重连时追加到已有 m3u8
- `hls_list_size`：滑动窗口（从 `CACHE_HOURS` 和 `SEGMENT_SEC` 计算）
- 文件名：`{会话前缀}_%06d.ts`（每段唯一，无碰撞）
- m3u8 和 `.ts` 文件都在 `cache/videos/` 目录下

### 冻结式 DVR 回放

进入 DVR 模式时：
1. `write_snapshot()` 直接克隆 `playlist.m3u8`，插入 `#EXT-X-PLAYLIST-TYPE:VOD`，追加 `#EXT-X-ENDLIST`
2. snapshot 与 segment 文件在同一目录（`cache/videos/`），mpv 直接解析
3. DVR 内部拖动只调 `mpv.seek()`，不重建快照，不重创播放器

### 平台支持

- 斗鱼：Flv 直播流 + MD5 多轮签名认证
- 虎牙：HLS 直播流 + 反盗链参数实时计算（`_build_anti_code`）

## 模块职责

### src/recording/ — 录制层

**douyu_stream_url.py** — 斗鱼流 URL 获取
- 查询开播状态（`/betard/{room_id}`）
- 获取播放地址（`/lapi/live/getH5Play/`）
- MD5 多轮签名认证

**huya_stream_url.py** — 虎牙流 URL 获取
- PC 页面 JSON + 小程序 API 双方法
- `_build_anti_code()` 实时计算 wsSecret/wsTime/seqid
- 优先取 HLS(m3u8) 流

**ffmpeg_recorder.py** — HLS 录制
- 管理 FFmpeg 子进程
- 健康检查：进程存活 + m3u8 mtime 变化检测
- stderr 后台线程采集
- 自动重连（最多 10 次，5s 间隔）
- 启动时 `taskkill` 清理孤儿 FFmpeg 进程（需配置 `bin/ffmpeg.exe` 路径）

### src/playback/ — 播放 + 缓存层

**cache_manager.py** — 缓存管理
- 每 3 秒扫描 FFmpeg 的 `playlist.m3u8`
- 轻量追踪：`_count`、`_cached_total`、`_first_ts`、`_last_ts`
- `total_duration()` O(1) 返回缓存值
- `write_snapshot()` — 直接克隆 m3u8（不重建）
- 孤儿 TS 清理：文件名自然排序，比 `_first_ts` 小的删除

**video_player.py** — mpv 播放器
- 封装 `python-mpv`，通过 `wid` 嵌入 PyQt6 QWidget
- 双模式：
  - 直播：`cache='no'` 低延迟
  - 回看：`cache='yes'` + `cache_secs=10` + `force_seekable` 流畅可跳转
- 33ms 定时轮询 position/duration
- seek 等待机制：等 mpv 解析完播放列表再执行跳转
- `reinitialize()` — 销毁旧实例 + 创建新实例 + 可选 sub_file + play
- `_destroy_mpv()` — 安全销毁，防止句柄泄漏

### src/danmaku/ — 弹幕层

**douyu_worker.js** — 斗鱼弹幕采集
- WebSocket STT 协议
- 输出 JSON 行到 stdout

**huya_worker.js** — 虎牙弹幕采集
- WebSocket Tars 协议 + 内联 JCE 编解码
- WSS 连接到 `cdnws.api.huya.com`

**collector.py** — 进程管理
- 管理 Node.js 子进程生命周期
- 按平台分派 worker JS
- 后台线程读取 stdout，解析 JSON

**manager.py** — 数据管理
- 时间戳转换（绝对 ms → 相对秒）
- `offset_sec < 0` 过滤
- NDJSON 持久化（`cache/danmaku/session_<ts>.ndjson`）
- 定期 flush（~3s）

**ass_writer.py** — ASS 字幕生成
- 弹幕碰撞检测：多条弹幕分轨显示
- `open_live()` — 行缓冲模式打开文件句柄
- `append_to_file()` — 直接写入，返回 compact 标志
- `_trim_live_file()` — 超过 2000 行时原子重写到 200 行
- `danmaku_to_ass()` — 从 NDJSON 生成完整 ASS 文件（DVR 使用）

**ass_exporter.py** — 切片导出
- `export_clip_ass()` — 从 NDJSON 生成指定时间范围的 ASS
- 导出使用 1280p PlayRes（性能优化）

**renderer.py** — 视频渲染
- `render_with_fallback()` — 依次尝试 h264_nvenc / h264_amf / h264_qsv / libx264

### src/ui/ — UI 层

**main_window.py** — 主窗口
- 连接/断开直播流（双流架构）
- DVR 进度条交互（LIVE→DVR / DVR→DVR / DVR→LIVE）
- 入点/出点标记 + MP4 导出到命名文件夹
- 导出状态栏常驻（`_exporting` 标志抑制缓存信息覆盖）
- 自动重连
- 弹幕协调：`_danmaku_live_start` 时间基准管理
- compact 时手动 sub_reload（不周期性 reload，无闪烁）

**slider.py** — 自定义进度条
- `SeekSlider(QSlider)` — 点击直接跳转 + 入出点标记线绘制

**workers.py** — 后台线程
- `StreamWorker` — 后台获取直播流 URL（支持斗鱼/虎牙）
- `ExportWorker` — FFmpeg concat 流复制导出
- `RenderWorker` — FFmpeg 弹幕烧录到视频
