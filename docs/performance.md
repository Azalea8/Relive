# ReLive 性能审计报告

> 审计日期: 2026-05-18  
> 审计范围: Python + Node.js 全链路  
> 录制规格: 4s/segment, hls_list_size=1800, 2 小时录制 ≈ 1800 segments

---

## 已修复 ✓

### P0 — total_duration() 缓存
`src/playback/cache_manager.py` — `_cached_total` 在 segment 变化时更新，`total_duration()` 直接返回 float，消除每秒 30 次 O(n) 求和。

### P1 — ASS 文件行缓冲句柄复用
`src/danmaku/ass_writer.py` — `open_live()` 用 `buffering=1` 保持句柄打开，`append_to_file()` 直接写入，行末 `\n` 自动 flush。每条弹幕从 3 次 syscall 降到 1 次。

### P1 — NDJSON 定期 flush
`src/danmaku/manager.py` — 去掉逐条 `flush()`，改为 `_on_segments_changed` 中调 `periodic_flush()`（~3s 间隔）。消除每秒数十次 fsync。

### P1 — 移除冗余 os.path.exists()
`src/playback/cache_manager.py` — `_parse_m3u8()` 不再对每个 segment 做 stat。FFmpeg HLS muxer 保证 m3u8 条目对应已存在文件。

### P2 — write_snapshot() 从内存构建
`src/playback/cache_manager.py` — 直接从 `self.segments` 构建 VOD m3u8，不读磁盘、不重新解析。

### P1/P3 — 移除无用内存弹幕存储
`src/danmaku/manager.py` — 删除 `_messages` / `_offsets` 列表、`query_range()`、`get_all()`。DVR 和导出都读 NDJSON 文件。仅保留 `_count` 计数器。

### P3 — AssWriter._items 清理
`src/danmaku/ass_writer.py` — `open_live()` 清空 `_items` 列表并 `reset_tracks()`，防止 live→DVR→live 循环中对象泄漏。

### 其他已修复
- `_on_go_live_url` AssWriter 创建两次 → 合并为一次
- 切回直播弹幕时间戳不匹配 → `_danmaku_live_start` 每次 go-live 重置
- `_on_segments_changed` 覆盖导出状态栏 → `_exporting` 标志抑制
- 2 小时缓存限制未生效 → `hls_list_size 1800 + delete_segments`

---

## 待优化（低优先级）

### P3 — _find_track() 每条弹幕 O(tracks) 扫描
`src/danmaku/ass_writer.py:88-110` — 每条弹幕遍历 ~25 个碰撞轨道。可接受。

### P3 — 1 秒 sub-reload timer
`src/ui/main_window.py` — mpv 每秒重新解析 ASS 文件。可接受。

### P3 — danmaku_to_ass() 主线程同步执行
`src/ui/main_window.py` — DVR 首次进入时在主线程读 NDJSON + 生成 ASS，约 100-200ms。

### 观察项

| 项目 | 位置 | 说明 |
|------|------|------|
| mpv 33ms poll timer | `src/playback/video_player.py` | 60 次 IPC/秒，可降频 |
| JS `catch(e){}` 静默丢弃 | `src/danmaku/douyu_worker.js` | 解析错误无诊断 |
| JS re2 替换 | `src/danmaku/douyu_worker.js` | 每次 STT 反序列化 2 次全局正则 |
| `time.time()` 在热路径 | 弹幕 + 位置更新 | 微小 syscall 开销 |
