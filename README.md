# ReLive

斗鱼直播 DVR 回放工具。基于 PyQt6 + mpv + FFmpeg，支持直播预览、弹幕、时移回看、片段导出。

## 功能

- **平台支持** — 斗鱼 （**其余平台，欢迎PR**），UI 顶部切换
- **直播预览** — mpv 实时播放直播流，支持直播模式全屏（隐藏 UI，ESC 退出）
- **弹幕采集** — Node.js 连接斗鱼弹幕 WebSocket，实时采集展示，录制为 NDJSON
- **DVR 时移回看** — 缓存时间可调（默认2小时），拖动进度条回看任意时间点，附带对应时间段弹幕 + 弹幕密度曲线
- **冻结快照** — 回看模式下克隆 FFmpeg 的 HLS m3u8 生成 VOD 快照
- **片段导出** — 设置 入点 / 出点 ，弹出命名对话框，导出 MP4 + 弹幕到指定文件夹
- **自动重连** — FFmpeg 录制中断后自动获取新 URL，使用 `append_list` 保留已有分段
- **设置面板** — 可调 缓存时长/弹幕字号/透明度 等，写入 config.json
- **暗色主题** — 中文 UI

## 使用

1. 选择平台，输入房间号，选择画质，点击「连接」
2. 直播画面自动播放，弹幕滚动显示
3. 点击全屏按钮进入直播全屏 （**仅ESC退出全屏**）
4. 拖动进度条进入 DVR 回看模式（带弹幕）
5. 点击「回到直播」或拖到进度条末端返回实时画面
6. 在回看模式下设置入点/出点，点击「导出」，命名文件夹

    生成 MP4（带弹幕） +  MP4（不带弹幕） +  切片ass弹幕文本


## 架构

```
录制层 (FFmpeg)      缓存层 (CacheManager)     播放层 (mpv)       弹幕层 (Danmaku)
──────────────────   ──────────────────────    ─────────────    ────────────────
直播流 → .ts + .m3u8  扫描 m3u8 → 计数+时长      直播低延迟/回看缓存  采集+展示+NDJSON
持续运行             周期扫描                  嵌入 PyQt6        Node.js 子进程
```

| 模块 | 职责 |
|------|------|
| `src/recording/douyu_stream_url.py` | 斗鱼直播流 URL 获取，MD5 签名认证 |
| `src/recording/huya_stream_url.py` | 虎牙直播流 URL 获取，反盗链实时计算 |
| `src/recording/ffmpeg_recorder.py` | FFmpeg 子进程，HLS 录制，健康检查，自动重连 |
| `src/playback/cache_manager.py` | 解析 FFmpeg 的 HLS m3u8，跟踪分段，生成 VOD 快照 |
| `src/playback/video_player.py` | mpv 封装，直播/回看双模式，嵌入 PyQt6 窗口 |
| `src/ui/main_window.py` | 主窗口 GUI，DVR 逻辑，导出功能，弹幕协调，全屏控制 |
| `src/ui/slider.py` | 自定义进度条，点击跳转 + 入出点标记线 |
| `src/ui/workers.py` | 后台线程：流 URL 获取、FFmpeg 导出、弹幕渲染 |
| `src/config.py` | 全局配置常量 + config.json 运行时覆盖 |
| `src/logger.py` | 日志系统 |
| `src/danmaku/douyu_worker.js` | 斗鱼弹幕采集 WebSocket |
| `src/danmaku/huya_worker.js` | 虎牙弹幕采集 WebSocket (Tars 协议) |
| `src/danmaku/collector.py` | 管理 Node.js 子进程，按平台分派 worker |
| `src/danmaku/manager.py` | 弹幕数据管理，NDJSON 持久化 |
| `src/danmaku/ass_writer.py` | ASS 字幕生成，弹幕飘动碰撞检测，live.ass 裁剪 |
| `src/danmaku/ass_exporter.py` | 切片弹幕 ASS 导出（1280p PlayRes 优化） |
| `src/danmaku/renderer.py` | FFmpeg 弹幕渲染到视频，编码器回退 |

## 环境要求

- Python >= 3.11
- FFmpeg（系统 PATH 或 `bin/ffmpeg.exe`）
- Node.js（弹幕采集）
- `libmpv-2.dll` 置于 `bin/` 目录

## 安装

```bash
uv sync
npm install
```

## 运行

```bash
uv run python main.py
```

## 项目结构

```
ReLive/
├── main.py                        # 入口
├── src/                           # Python 包
│   ├── config.py                  # 配置常量 + config.json
│   ├── logger.py                  # 日志系统
│   ├── recording/                 # 录制层
│   │   ├── douyu_stream_url.py    # 斗鱼流 URL
│   │   ├── huya_stream_url.py     # 虎牙流 URL
│   │   └── ffmpeg_recorder.py     # FFmpeg HLS 录制
│   ├── playback/                  # 播放 + 缓存层
│   │   ├── video_player.py        # mpv 播放器
│   │   └── cache_manager.py       # m3u8 解析 + 快照
│   ├── danmaku/                   # 弹幕层
│   │   ├── douyu_worker.js        # 斗鱼弹幕采集
│   │   ├── huya_worker.js         # 虎牙弹幕采集
│   │   ├── collector.py           # Node 子进程管理
│   │   ├── manager.py             # 数据 + NDJSON
│   │   ├── ass_writer.py          # ASS 字幕生成
│   │   ├── ass_exporter.py        # 切片 ASS 导出
│   │   └── renderer.py            # FFmpeg 弹幕渲染
│   └── ui/                        # UI 层
│       ├── main_window.py         # 主窗口
│       ├── slider.py              # 自定义进度条
│       └── workers.py             # 后台工作线程
├── bin/
│   ├── libmpv-2.dll               # mpv 动态库
│   ├── ffmpeg.exe                 # FFmpeg（手动放置或者系统PATH）
│   └── node.exe                   # Node.js（手动放置或者系统PATH）
├── docs/                          # 开发文档
│   ├── architecture.md            # 架构设计
│   ├── dvr-design.md              # DVR 回放设计
│   └── performance.md             # 性能审计
├── pyproject.toml
├── package.json
└── README.md
```
