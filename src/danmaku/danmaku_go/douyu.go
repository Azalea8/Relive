package main

import (
	"crypto/tls"
	"encoding/binary"
	"fmt"
	"log"
	"math/rand"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/gorilla/websocket"
)

var ports = []int{8501, 8502, 8503, 8504, 8505, 8506}

// 自动探测代理，结果缓存（只探测一次）
var autoProxyFunc func(*http.Request) (*url.URL, error)

func getAutoProxy() func(*http.Request) (*url.URL, error) {
	if autoProxyFunc != nil {
		return autoProxyFunc
	}

	// 候选代理列表（按需增删）
	candidates := []string{
		"http://127.0.0.1:7890",   // Clash 默认 HTTP
		"http://127.0.0.1:10809",  // V2Ray 默认 HTTP
		"http://127.0.0.1:1080",   // 通用
		"socks5://127.0.0.1:7890", // 如需 SOCKS5 支持
	}

	for _, proxyStr := range candidates {
		proxyURL, err := url.Parse(proxyStr)
		if err != nil {
			continue
		}
		host := proxyURL.Hostname()
		port := proxyURL.Port()
		if port == "" {
			port = "80"
		}
		// 快速 TCP 探测（超时 300ms）
		conn, err := net.DialTimeout("tcp", net.JoinHostPort(host, port), 300*time.Millisecond)
		if err == nil {
			conn.Close()
			// 找到可用代理，返回 ProxyURL 函数
			autoProxyFunc = http.ProxyURL(proxyURL)
			log.Printf("[AUTO-PROXY] 使用代理: %s", proxyStr)
			return autoProxyFunc
		}
	}

	// 无代理，回退到环境变量（或 nil）
	autoProxyFunc = http.ProxyFromEnvironment
	log.Println("[AUTO-PROXY] 未检测到代理，将直连")
	return autoProxyFunc
}

func init() {
	suites := tls.InsecureCipherSuites()
	ids := make([]uint16, len(suites))
	for i, s := range suites {
		ids[i] = s.ID
	}
	douyuDialer = &websocket.Dialer{
		TLSClientConfig: &tls.Config{
			InsecureSkipVerify: true,
			MinVersion:         tls.VersionTLS10,
			CipherSuites:       ids,
		},
		HandshakeTimeout: 10 * time.Second,
		Proxy:            getAutoProxy(), // 代理自动探测函数
	}
}

var douyuDialer *websocket.Dialer

var colorTab = map[string]string{
	"1": "ff2e2e", "2": "00ccff", "3": "66ff00",
	"4": "ff6600", "5": "cc00ff", "6": "f6447f",
}

func runDouyu(roomID string, logger *log.Logger) {
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

	reconnectCount := 0
	for {
		cancel := make(chan struct{})
		errCh := make(chan error, 1)
		go func() { errCh <- connectDouyu(roomID, logger, cancel) }()

		select {
		case <-sigCh:
			logger.Println("收到退出信号")
			close(cancel)
			return
		case err := <-errCh:
			if err != nil {
				reconnectCount++
				if reconnectCount > 20 {
					logger.Fatalf("重连失败超过20次")
				}
				logger.Printf("连接断开，%ds 后重连 (%d/20)", 3, reconnectCount)
				select {
				case <-sigCh:
					logger.Println("收到退出信号")
					return
				case <-time.After(3 * time.Second):
				}
			} else {
				return
			}
		}
	}
}

func connectDouyu(roomID string, logger *log.Logger, cancel chan struct{}) error {
	port := ports[rand.Intn(len(ports))]
	url := fmt.Sprintf("wss://danmuproxy.douyu.com:%d/", port)
	logger.Printf("连接 %s room=%s", url, roomID)

	conn, _, err := douyuDialer.Dial(url, nil)
	if err != nil {
		logger.Printf("Dial 失败: %v", err)
		return err
	}
	defer conn.Close()
	logger.Println("已连接")

	conn.WriteMessage(websocket.BinaryMessage, encodeSTT(map[string]string{
		"type": "loginreq", "roomid": roomID,
	}))
	conn.WriteMessage(websocket.BinaryMessage, encodeSTT(map[string]string{
		"type": "joingroup", "rid": roomID, "gid": "-9999",
	}))

	done := make(chan struct{})
	defer close(done)
	go func() {
		ticker := time.NewTicker(45 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-done:
				return
			case <-cancel:
				return
			case <-ticker.C:
				conn.WriteMessage(websocket.BinaryMessage, encodeSTT(map[string]string{"type": "mrkl"}))
			}
		}
	}()

	readErr := make(chan error, 1)
	go func() {
		for {
			_, data, err := conn.ReadMessage()
			if err != nil {
				readErr <- err
				return
			}
			decodeMessages(data, func(raw string) {
				msg := sttDecode(raw)
				if msg == nil || msg["type"] != "chatmsg" {
					return
				}
				color := "ffffff"
				if c, ok := colorTab[msg["col"]]; ok {
					color = c
				}
				emit(danmakuMsg{
					TimestampMs: time.Now().UnixMilli(),
					Content:     msg["txt"],
					Username:    msg["nn"],
					MsgType:     "chat",
					Color:       color,
					UID:         msg["uid"],
				})
			})
		}
	}()

	select {
	case <-cancel:
		return nil
	case err := <-readErr:
		return err
	}
}

func sttEscape(v string) string {
	v = strings.ReplaceAll(v, "@", "@A")
	return strings.ReplaceAll(v, "/", "@S")
}

func sttUnescape(v string) string {
	v = strings.ReplaceAll(v, "@S", "/")
	return strings.ReplaceAll(v, "@A", "@")
}

func encodeSTT(obj map[string]string) []byte {
	var b strings.Builder
	for k, v := range obj {
		b.WriteString(k)
		b.WriteString("@=")
		b.WriteString(sttEscape(v))
		b.WriteString("/")
	}
	body := b.String() + "\x00"
	msgLen := len(body) + 8

	buf := make([]byte, 12+len(body))
	binary.LittleEndian.PutUint32(buf[0:4], uint32(msgLen))
	binary.LittleEndian.PutUint32(buf[4:8], uint32(msgLen))
	binary.LittleEndian.PutUint16(buf[8:10], 689)
	binary.LittleEndian.PutUint16(buf[10:12], 0)
	copy(buf[12:], body)
	return buf
}

func decodeMessages(data []byte, cb func(string)) {
	offset := 0
	for offset < len(data) {
		if offset+4 > len(data) {
			break
		}
		readLen := int(binary.LittleEndian.Uint32(data[offset : offset+4]))
		offset += 4
		if offset+readLen-4 > len(data) {
			break
		}
		body := data[offset+8 : offset+readLen-1]
		offset += readLen - 4
		cb(string(body))
	}
}

func sttDecode(raw string) map[string]string {
	if raw == "" {
		return nil
	}
	result := make(map[string]string)
	pairs := strings.Split(strings.TrimRight(raw, "/"), "/")
	for _, pair := range pairs {
		idx := strings.Index(pair, "@=")
		if idx == -1 {
			continue
		}
		k := pair[:idx]
		v := pair[idx+2:]
		result[k] = sttUnescape(v)
	}
	return result
}
