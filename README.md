# ReLive

斗鱼直播 DVR 回放工具。基于 PyQt6 + mpv + FFmpeg，支持直播预览、时移回看、片段导出。

## 功能

- **直播预览** — mpv 实时播放直播流
- **DVR 时移回看** — 最多 2 小时缓存，拖动进度条回看任意时间点
- **冻结时间轴** — 回看模式下时间轴固定，内部 seek 不重建快照
- **片段导出** — 设置入点/出点，导出为 MP4
- **自动重连** — FFmpeg 录制中断后自动获取新 URL 重连

## 架构

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  stream_url  │────▶│ ffmpeg_recorder│────▶│   cache/    │
│  (斗鱼API)   │     │  (HLS录制)    │     │  .ts + .m3u8│
└─────────────┘     └──────────────┘     └──────┬──────┘
                                                │
┌─────────────┐     ┌──────────────┐     ┌──────▼──────┐
│ main_window  │────▶│ video_player  │◀────│cache_manager│
│  (PyQt6 GUI) │     │   (mpv)      │     │ (segment解析)│
└─────────────┘     └──────────────┘     └─────────────┘
```

| 模块 | 职责 |
|------|------|
| `stream_url.py` | 斗鱼直播流 URL 获取，MD5 签名认证 |
| `ffmpeg_recorder.py` | FFmpeg 子进程管理，HLS 录制，健康检查 |
| `cache_manager.py` | m3u8 解析，segment 跟踪，冻结快照生成 |
| `video_player.py` | mpv 封装，嵌入 PyQt6 窗口，seek/播放控制 |
| `main_window.py` | 主窗口 GUI，DVR 逻辑，导出功能 |
| `config.py` | 全局配置常量 |
| `logger.py` | 日志系统 |

## 环境要求

- Python >= 3.11
- FFmpeg（系统 PATH 或 `bin/ffmpeg.exe`）
- Node.js（弹幕采集，可选）

## 安装

```bash
# Python 依赖
uv sync

# Node.js 依赖（弹幕功能）
npm install
```

需要 `libmpv-2.dll` 放在 `bin/` 目录下。

## 运行

```bash
uv run python main.py
```

## 使用

1. 输入斗鱼房间号，点击「连接」
2. 直播画面自动播放，进度条实时跟踪
3. 拖动进度条进入 DVR 回看模式
4. 点击「回到直播」返回实时画面
5. 设置入点/出点，点击「导出」保存 MP4

## 项目结构

```
ReLive/
├── main.py              # 入口
├── config.py            # 配置
├── logger.py            # 日志
├── stream_url.py        # 斗鱼流 URL
├── ffmpeg_recorder.py   # FFmpeg 录制
├── cache_manager.py     # 缓存管理
├── video_player.py      # mpv 播放器
├── main_window.py       # 主窗口
├── core/
│   └── danmaku/
│       └── douyu_worker.js  # 弹幕采集
├── bin/
│   └── libmpv-2.dll     # mpv 动态库
├── docs/                # 开发文档
├── pyproject.toml       # Python 项目配置
└── package.json         # Node.js 依赖
```
