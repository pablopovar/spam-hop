package transport

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"time"
)

type ProcessorClient struct {
	url    string
	client *http.Client
}

func NewProcessorClient(url string) *ProcessorClient {
	return &ProcessorClient{
		url: url,
		client: &http.Client{
			Timeout: 120 * time.Second,
		},
	}
}

func (p *ProcessorClient) Process(
	ctx context.Context,
	message []byte,
) ([]byte, error) {
	req, err := http.NewRequestWithContext(
		ctx,
		http.MethodPost,
		p.url+"/process",
		bytes.NewReader(message),
	)
	if err != nil {
		return nil, err
	}

	req.Header.Set("Content-Type", "message/rfc822")

	resp, err := p.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	switch resp.StatusCode {
	case http.StatusNoContent:
		return message, nil

	case http.StatusOK:
		return io.ReadAll(resp.Body)

	default:
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))

		return nil, fmt.Errorf(
			"processor returned %s: %s",
			resp.Status,
			string(body),
		)
	}
}
