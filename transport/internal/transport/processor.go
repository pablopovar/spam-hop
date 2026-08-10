package transport

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

type ScoreResult struct {
	Score   *float64 `json:"score"`
	Verdict string   `json:"verdict"`
	Engine  string   `json:"engine"`
}

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

func (p *ProcessorClient) Score(
	ctx context.Context,
	message []byte,
) (ScoreResult, error) {
	req, err := http.NewRequestWithContext(
		ctx,
		http.MethodPost,
		p.url+"/score",
		bytes.NewReader(message),
	)
	if err != nil {
		return ScoreResult{}, err
	}

	req.Header.Set("Content-Type", "message/rfc822")

	resp, err := p.client.Do(req)
	if err != nil {
		return ScoreResult{}, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return ScoreResult{}, fmt.Errorf(
			"processor returned %s: %s",
			resp.Status,
			string(body),
		)
	}

	var result ScoreResult
	decoder := json.NewDecoder(io.LimitReader(resp.Body, 1<<20))
	if err := decoder.Decode(&result); err != nil {
		return ScoreResult{}, fmt.Errorf("decode processor result: %w", err)
	}

	return result, nil
}
