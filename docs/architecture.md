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
持续运行             周期扫描                   嵌入 PyQt6         Go 子进程
```

### 双流并行

直播模式下两路独立运行：
- **FFmpeg** 录制到本地 `cache/videos/` 目录（HLS 分段 + m3u8）
- **mpv** 直接播放直播 URL（实时预览 + 弹幕 ASS 字幕）

### HLS 录制

FFmpeg 使用 `-f hls -hls_flags append_list -hls_list_size N`：
- `append_list`：重连时追加到已有 m3u8
- `hls_list_size`：滑动窗口（从 `CACHE_HOURS` 和 `SEGMENT_SEC` 计算）
- 文件名：`{会话前缀}_%06d.ts`（每段唯一，无碰撞）
- m3u8 和 `.ts` 文件都在 `cache/videos/` 目录下
- FFmpeg 录制时可传入 `-headers` 携带平台特定的 HTTP 头（User-Agent / Origin / Referer），避免 CDN 拦截

### 冻结式 DVR 回放

进入 DVR 模式时：
1. `write_snapshot()` 直接克隆 `playlist.m3u8`，插入 `#EXT-X-PLAYLIST-TYPE:VOD`，追加 `#EXT-X-ENDLIST`
2. snapshot 与 segment 文件在同一目录（`cache/videos/`），mpv 直接解析
3. DVR 内部拖动只调 `mpv.seek()`，不重建快照，不重创播放器

### 平台支持

通过 `src/recording/__init__.py` 中的 `PLATFORMS` 注册表管理，每个平台模块实现统一接口：
- `check_live(room_id) -> bool`：检查开播状态
- `get_stream_url(room_id) -> str | None`：获取直播流 URL（默认最高画质）
- `RECORD_HEADERS: str`：FFmpeg `-headers` 参数（`\r\n` 分隔）
- `MPV_HEADER_FIELDS: str`：mpv `http-header-fields` 参数（逗号分隔）

新增平台只需：实现上述接口 + 在 `PLATFORMS` 中注册 + UI combo 添加选项。

| 平台 | 流格式 | 签名方式 |
|------|--------|----------|
| 斗鱼 | FLV | MD5 多轮签名 |
| 虎牙 | FLV | 反盗链（wsSecret / convert_uid） |
| B站 | HLS TS | 官方 API v2，可选 cookie 获取原画 |
| 抖音 | FLV | 页面 JSON 解析，URL 自带过期参数 |

## 模块职责

### src/recording/ — 录制层

**`__init__.py`** — 平台注册表
- 定义 `PLATFORMS` 字典，按平台名索引对应模块
- 后续加平台只需在此注册一行

**douyu_stream_url.py** — 斗鱼流 URL 获取
- 查询开播状态（`/betard/{room_id}`）
- 获取加密参数（`/wgapi/livenc/liveweb/websec/getEncryption`）
- MD5 多轮签名认证
- 请求播放地址（`/lapi/live/getH5PlayV1/`）

**huya_stream_url.py** — 虎牙流 URL 获取
- 从 PC 页面 HTML 提取 `stream:` JSON（brace counting 解析）
- 使用 streamlink 反盗链算法：`convert_uid` 字节交换 + wsSecret MD5 计算
- wsTime 复用 anti-code 中原值，不自算
- 优先 TX CDN，取 source 画质（`iBitRate=0`）

**bilibili_stream_url.py** — B站流 URL 获取
- 调用官方 API v2 `xlive/web-room/v2/index/getRoomPlayInfo`
- 无 cookie 时仅获 720P（`qn=250`），配置 `BILIBILI_COOKIE` 后可获原画
- 取 HLS TS AVC 流

**douyin_stream_url.py** — 抖音流 URL 获取
- 从 PC 页面 HTML 提取 `self.__pace_f.push()` 数据
- 解析 `roomStore.roomInfo.room.stream_url.flv_pull_url`
- 按优先级取画质：FULL_HD1 > HD1 > SD1 > SD2
- URL 自带 `expire` / `sign` 参数，无需额外签名

**ffmpeg_recorder.py** — HLS 录制
- 管理 FFmpeg 子进程
- 传入平台特定的 `-headers`（User-Agent / Origin / Referer）
- 健康检查：进程存活 + m3u8 mtime 变化检测
- stderr 后台线程采集到 200 行循环缓冲区
- 自动重连（最多 10 次，5s 间隔）

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
- 传入 `http_header_fields` 以绕过 CDN User-Agent 检测

### src/danmaku/ — 弹幕层

**danmaku_go/** — Go 弹幕采集器
- `main.go`：入口，按平台路由到对应采集函数
- `douyu.go`：斗鱼 WebSocket STT 协议，gorilla/websocket + TLS 兼容配置
- `douyin.go`：抖音 WebSocket Protobuf 协议，基于 jwwsjlm/douyinLive SDK
- 输出 JSON 行到 stdout，格式统一：`{"timestamp_ms":..., "content":"...", "username":"...", "msg_type":"chat", "color":"ffffff", "uid":"..."}`
- 编译为 `bin/danmaku_worker.exe`，用法 `danmaku_worker.exe <douyin|douyu> <room_id>`

**collector.py** — 进程管理
- 管理 Go 子进程生命周期
- 按平台分派：douyin/douyu 走 Go，其他平台返回 None
- 开发模式：`go run . <platform> <room_id>` 在 `danmaku_go` 目录执行
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
- `render_with_fallback()` — 依次尝试 h264_nvenc / h264_amf / h264_qsv / h264_videotoolbox / h264_vaapi / libx264

### src/ui/ — UI 层

**main_window.py** — 主窗口
- 连接/断开直播流（双流架构）
- 平台注册表集成，新增平台只需加 combo 项 + `_platform_keys`
- B站 cookie 弹窗（首次连接时提示输入，持久化到 config.json）
- DVR 进度条交互（LIVE→DVR / DVR→DVR / DVR→LIVE）
- 入点/出点标记 + MP4 导出到命名文件夹
- 导出状态栏常驻（`_exporting` 标志抑制缓存信息覆盖）
- 自动重连
- 弹幕协调：`_danmaku_live_start` 时间基准管理
- compact 时手动 sub_reload（不周期性 reload，无闪烁）
- 全屏：直播模式专用按钮（弹幕和设置之间），回看置灰；隐藏上下 chrome + 状态栏，ESC 退出
- 弹幕密度曲线：回看时自动读取 NDJSON 生成 2s 桶折线图，显示在进度条上方

**slider.py** — 自定义进度条 + 密度叠加层
- `SeekSlider(QSlider)` — 点击直接跳转 + 入出点标记线绘制
- `DensityOverlay(QWidget)` — 48px 透明层叠放在滑块上方，Catmull-Rom 样条平滑曲线 + 抗锯齿

**workers.py** — 后台线程
- `StreamWorker` — 后台获取直播流 URL，通过 PLATFORMS 注册表分派
- `ExportWorker` — FFmpeg concat 流复制导出
- `RenderWorker` — FFmpeg 弹幕烧录到视频
