package main

import (
	"log"
	"os"
	"os/signal"
	"strconv"
	"syscall"

	douyinlive "github.com/jwwsjlm/douyinLive/v2"
	"github.com/jwwsjlm/douyinLive/v2/generated/new_douyin"
	"google.golang.org/protobuf/proto"
)

func runDouyin(roomID string, logger *log.Logger) {
	dl, err := douyinlive.NewDouyinLive(roomID, logger, "")
	if err != nil {
		logger.Fatalf("创建失败: %v", err)
	}

	dl.SubscribeMethod(douyinlive.WebcastChatMessage, func(msg *douyinlive.LiveMessage) {
		chat := &new_douyin.Webcast_Im_ChatMessage{}
		if err := proto.Unmarshal(msg.GetPayload(), chat); err != nil {
			return
		}
		if chat.GetContent() == "" {
			return
		}
		username := ""
		uid := ""
		if chat.GetUser() != nil {
			username = chat.GetUser().GetNickname()
			uid = formatID(chat.GetUser().GetId())
		}
		emit(danmakuMsg{
			TimestampMs: msg.ReceivedAt.UnixMilli(),
			Content:     chat.GetContent(),
			Username:    username,
			MsgType:     "chat",
			Color:       "ffffff",
			UID:         uid,
		})
	})

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

	errCh := make(chan error, 1)
	go func() { errCh <- dl.Start() }()

	select {
	case <-sigCh:
		logger.Println("收到退出信号")
	case err := <-errCh:
		if err != nil {
			logger.Fatalf("连接异常: %v", err)
		}
	}
}

func formatID(id uint64) string {
	if id == 0 {
		return ""
	}
	return strconv.FormatUint(id, 10)
}
