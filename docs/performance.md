# ReLive 性能审计报告

> 审计日期: 2026-05-20  
> 审计范围: Python + Go 全链路  
> 录制规格: 4s/segment, hls_list_size=1800, 2 小时录制 ≈ 1800 segments

---

## 已修复

### P0 — total_duration() 缓存
`src/playback/cache_manager.py` — `_cached_total` 在 segment 变化时更新，`total_duration()` O(1) 返回。

### P0 — CacheManager 轻量化
删掉 `self.segments` 列表，替换为 4 个标量：`_count`、`_cached_total`、`_first_ts`、`_last_ts`。

### P1 — ASS 文件行缓冲句柄复用
`src/danmaku/ass_writer.py` — `open_live()` 用 `buffering=1` 保持句柄打开。

### P1 — NDJSON 定期 flush
`src/danmaku/manager.py` — 去掉逐条 `flush()`，改为 `_on_segments_changed` 中调 `periodic_flush()`。

### P1 — 移除冗余 os.path.exists()
`src/playback/cache_manager.py` — `_scan_m3u8()` 不再做 stat 检查。

### P1 — 孤儿 TS 清理
用 `_first_ts` 字符串比较（文件名自然排序），`sorted()` + `break` 早停。

### P1 — 删除内存弹幕存储
`src/danmaku/manager.py` — 删除 `_messages` / `_offsets` 列表。DVR 和导出都读 NDJSON 文件。

### P1 — 删除 sub-reload 周期定时器
`src/ui/main_window.py` — 删除每秒 `sub_reload`，mpv 行缓冲写入自动感知。

### P2 — live.ass 裁剪
超过 2000 行时原子重写到 200 行（tempfile + os.replace）。

### P2 — AssWriter._items 删除
`src/danmaku/ass_writer.py` — 每条弹幕不再累积到列表。

### P3 — 导出 1280p PlayRes
`src/danmaku/ass_exporter.py` — 导出 ASS 用 1280x720 PlayRes，fontsize 等比缩放。libass glyph 渲染面积 ~44% 减少。

### P3 — video_player 双模式
直播 `cache='no'` 零缓冲；回看 `cache_secs=10` + `force_seekable`。

### 其他已修复
- segment 文件名从 strftime 改为 session_prefix + %06d — 无碰撞
- m3u8 变化检测从 file-size 改为 last_ts 字符串比较
- _on_go_live_url AssWriter 创建两次 → 合并
- _danmaku_live_start 独立时间基准
- 导出状态栏 _exporting 标志抑制
- _on_connect 用 _connected 判断断开
- 关闭窗口直接 taskkill FFmpeg
- render 和 export worker 异常捕获
- render 去掉 -hwaccel 避免 GPU 冲突
- FFmpeg 导出/渲染 stderr 写日志
- 直播模式全屏（仅 ESC 退出，回看禁用）
- 弹幕密度曲线（Catmull-Rom 平滑 + 抗锯齿）

---

## 待优化（低优先级）

| 项目 | 位置 |
|------|------|
| `_find_track()` O(tracks) 碰撞检测 | `ass_writer.py` |
| `danmaku_to_ass()` 主线程同步执行 | `main_window.py` |
| mpv 33ms poll timer | `video_player.py` |
