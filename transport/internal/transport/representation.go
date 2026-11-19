package transport

import (
	"bytes"
	"fmt"
	"strings"
)

func cleanHeaderValue(value string) string {
	value = strings.ReplaceAll(value, "\r", " ")
	value = strings.ReplaceAll(value, "\n", " ")
	return strings.TrimSpace(value)
}

// BuildRepresentation adds SpamHop scoring metadata to the downstream
// representation only. The upstream mailbox object remains untouched.
func BuildRepresentation(message []byte, score ScoreResult) []byte {
	var headers []string

	if score.Score != nil {
		headers = append(headers, fmt.Sprintf("X-SpamHop-Score: %.4f", *score.Score))
	}
	if verdict := cleanHeaderValue(score.Verdict); verdict != "" {
		headers = append(headers, "X-SpamHop-Verdict: "+verdict)
	}
	if engine := cleanHeaderValue(score.Engine); engine != "" {
		headers = append(headers, "X-SpamHop-Engine: "+engine)
	}

	if len(headers) == 0 {
		return message
	}

	separator := []byte("\r\n\r\n")
	lineEnd := "\r\n"
	index := bytes.Index(message, separator)
	if index < 0 {
		separator = []byte("\n\n")
		lineEnd = "\n"
		index = bytes.Index(message, separator)
	}
	if index < 0 {
		return message
	}

	block := []byte(lineEnd + strings.Join(headers, lineEnd))
	result := make([]byte, 0, len(message)+len(block))
	result = append(result, message[:index]...)
	result = append(result, block...)
	result = append(result, message[index:]...)
	return result
}
