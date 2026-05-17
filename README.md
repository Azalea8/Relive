# ReLive

斗鱼直播 DVR 回放工具。基于 PyQt6 + mpv + FFmpeg，支持直播预览、弹幕、时移回看、片段导出。

## 功能

- **直播预览** — mpv 实时播放直播流，附带滚动弹幕
- **弹幕采集** — Node.js 连接斗鱼弹幕 WebSocket，实时采集展示，录制为 NDJSON
- **DVR 时移回看** — 最多 2 小时缓存，拖动进度条回看任意时间点，附带对应时间段弹幕
- **冻结快照** — 回看模式下从 FFmpeg 的 HLS m3u8 生成 VOD 快照，内部 seek 不重建
- **片段导出** — 设置入点/出点，弹出命名对话框，导出 MP4 + 弹幕到指定文件夹
- **自动重连** — FFmpeg 录制中断后自动获取新 URL，使用 `append_list` 保留已有分段
- **暗色主题** — 中文 UI，斗鱼风格配色

## 架构

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  stream_url  │────▶│ ffmpeg_recorder│────▶│   cache/    │
│  (斗鱼API)   │     │  (HLS+append) │     │.ts + .m3u8 │
└─────────────┘     └──────────────┘     └──────┬──────┘
                                                │
┌─────────────┐     ┌──────────────┐     ┌──────▼──────┐
│ main_window  │────▶│ video_player  │◀────│cache_manager│
│  (PyQt6 GUI) │     │   (mpv)      │     │ (m3u8解析)  │
└──────┬──────┘     └──────────────┘     └─────────────┘
       │
┌──────▼──────┐     ┌──────────────┐
│danmaku_collector│──▶│danmaku_manager│
│ (Node.js子进程) │     │ (NDJSON+ASS) │
└─────────────┘     └──────────────┘
```

| 模块 | 职责 |
|------|------|
| `stream_url.py` | 斗鱼直播流 URL 获取，MD5 签名认证 |
| `ffmpeg_recorder.py` | FFmpeg 子进程管理，`-f hls + append_list` 录制，健康检查 |
| `cache_manager.py` | 解析 FFmpeg 的 HLS m3u8，跟踪分段，生成 VOD 快照 |
| `video_player.py` | mpv 封装，嵌入 PyQt6 窗口，seek/播放控制 |
| `main_window.py` | 主窗口 GUI，DVR 逻辑，导出功能，弹幕协调 |
| `config.py` | 全局配置常量 |
| `logger.py` | 日志系统 |
| `core/danmaku/douyu_worker.js` | Node.js 弹幕采集，连接斗鱼 WebSocket |
| `core/danmaku/danmaku_collector.py` | 管理 Node.js 子进程，解析 stdout JSON |
| `core/danmaku/danmaku_manager.py` | 弹幕数据管理，NDJSON 持久化，时间范围查询 |
| `core/danmaku/ass_writer.py` | ASS 字幕生成，弹幕飘动碰撞检测 |
| `core/danmaku/ass_exporter.py` | 切片弹幕 ASS 导出 |
| `core/danmaku/dm_renderer.py` | FFmpeg 弹幕渲染到视频 |

## 环境要求

- Python >= 3.11
- FFmpeg（系统 PATH 或 `bin/ffmpeg.exe`）
- Node.js（弹幕采集）
- `libmpv-2.dll` 置于 `bin/` 目录

## 安装

```bash
# Python 依赖
uv sync

# Node.js 依赖（弹幕功能）
npm install
```

## 运行

```bash
uv run python main.py
```

## 使用

1. 输入斗鱼房间号，选择画质，点击「连接」
2. 直播画面自动播放，弹幕滚动显示，进度条跟踪缓存
3. 拖动进度条进入 DVR 回看模式（带弹幕）
4. 点击「回到直播」或拖到进度条末端返回实时画面
5. 在回看模式下设置入点/出点，点击「导出」，命名文件夹，生成 MP4

## 项目结构

```
ReLive/
├── main.py              # 入口
├── config.py            # 配置常量
├── logger.py            # 日志系统
├── stream_url.py        # 斗鱼流 URL 解析
├── ffmpeg_recorder.py   # FFmpeg HLS 录制
├── cache_manager.py     # m3u8 解析 + 快照生成
├── video_player.py      # mpv 播放器封装
├── main_window.py       # 主窗口 + DVR + 导出 + 弹幕协调
├── core/
│   └── danmaku/
│       ├── douyu_worker.js      # 弹幕采集 Node.js 进程
│       ├── danmaku_collector.py # Node 子进程管理
│       ├── danmaku_manager.py   # 弹幕数据 + NDJSON
│       ├── ass_writer.py        # ASS 字幕生成
│       ├── ass_exporter.py      # 切片 ASS 导出
│       └── dm_renderer.py       # FFmpeg 弹幕渲染
├── bin/
│   └── libmpv-2.dll     # mpv 动态库
├── docs/                # 开发文档
│   ├── architecture.md  # 架构设计
│   ├── dvr-design.md    # DVR 回放设计
│   └── performance.md   # 性能审计报告
├── pyproject.toml       # Python 项目配置
└── package.json         # Node.js 依赖
```
