// danmaku_worker — 弹幕采集，输出 JSON 行到 stdout
// 用法: danmaku_worker <douyin|douyu> <room_id>

package main

import (
	"encoding/json"
	"log"
	"os"
)

type danmakuMsg struct {
	TimestampMs int64  `json:"timestamp_ms"`
	Content     string `json:"content"`
	Username    string `json:"username"`
	MsgType     string `json:"msg_type"`
	Color       string `json:"color"`
	UID         string `json:"uid"`
}

func main() {
	if len(os.Args) < 3 || os.Args[2] == "" {
		os.Stderr.WriteString("Usage: danmaku_worker <douyin|douyu> <room_id>\n")
		os.Exit(1)
	}
	platform := os.Args[1]
	roomID := os.Args[2]

	logger := log.New(os.Stderr, "["+platform+"] ", log.Ldate|log.Ltime)

	switch platform {
	case "douyin":
		runDouyin(roomID, logger)
	case "douyu":
		runDouyu(roomID, logger)
	default:
		logger.Fatalf("未知平台: %s (支持 douyin, douyu)", platform)
	}
}

func emit(msg danmakuMsg) {
	data, _ := json.Marshal(msg)
	os.Stdout.Write(data)
	os.Stdout.Write([]byte("\n"))
}
