package main

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	douyinLive "github.com/jwwsjlm/douyinLive/v2"
	"google.golang.org/protobuf/encoding/protojson"
)

type event struct {
	Method     string          `json:"method"`
	MessageID  uint64          `json:"message_id"`
	ReceivedAt float64         `json:"received_at"`
	Payload    json.RawMessage `json:"payload"`
}

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "usage: live-product-bridge <room-id>")
		os.Exit(2)
	}

	live, err := douyinLive.NewDouyinLiveWithSlog(os.Args[1], slog.New(slog.NewTextHandler(os.Stderr, nil)), "")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	defer live.Dispose()

	var outputMu sync.Mutex
	live.SubscribeMethod("WebcastProductChangeMessage", func(message *douyinLive.LiveMessage) {
		if message == nil || message.Raw == nil || message.Parsed == nil {
			return
		}
		payload, marshalErr := protojson.Marshal(message.Parsed)
		if marshalErr != nil {
			return
		}
		outputMu.Lock()
		defer outputMu.Unlock()
		_ = json.NewEncoder(os.Stdout).Encode(event{
			Method: message.GetMethod(), MessageID: message.Raw.MsgId,
			ReceivedAt: float64(message.ReceivedAt.UnixNano()) / float64(time.Second),
			Payload:    payload,
		})
	})
	if err := live.Start(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}

	stopped := make(chan os.Signal, 1)
	signal.Notify(stopped, os.Interrupt, syscall.SIGTERM)
	<-stopped
}
